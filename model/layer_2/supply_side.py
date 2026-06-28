###############################################
## Case study: Forecasting of energy prices
###############################################
## Layer 2: Supply side
# supply is generation + net imports

## minimum data:
# library
import pandas as pd
import numpy as np
import datetime
import statsmodels.api as sm
import matplotlib.pyplot as plt
import seaborn as sns
from statsmodels.tsa.stattools import adfuller, acf, pacf
from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
from statsmodels.tsa.seasonal import seasonal_decompose
from pmdarima.arima import auto_arima
from sklearn import linear_model
from sklearn.tree import DecisionTreeRegressor
from sklearn.model_selection import cross_val_score
from sklearn.ensemble import RandomForestRegressor
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_squared_error
from sklearn.metrics import root_mean_squared_error
from sklearn.metrics import mean_absolute_error
from sklearn.metrics import mean_absolute_percentage_error

# supress scientific notation
pd.options.display.float_format ='{:.6f}'.format
 
# path
path_data = r'C:\Users\Kamelia\Desktop\SU\PhD\Courses\Summer school 2026\Case study\Data\entsoe_bg'
path_results = r'C:\Users\Kamelia\Desktop\SU\PhD\Courses\Summer school 2026\Case study'

# load data
# we need the ts 
target = pd.read_csv(path_data + r'\generation_per_type (1).csv', sep=',')
print(target.shape)
print(target.columns)
print(target.dtypes)
# covert time stamp to ts (UTC?)
target['datetime'] = pd.to_datetime(target['timestamp'], utc=True)
#target['ch'] = target['datetime'].diff()
#target['ch'].describe()
#print(target['ch'].unique())
# difference of two hours?
#ch = target[target.ch.isin(['0 days 02:00:00'])][['timestamp','datetime','ch']]
# one obs
# index 2042 is goes from +2 to +3
# it seems to be correctly converted, but we miss 1 observation there
# check na
print(target.isna().sum())
# only in the hydro storage
# fill missing values
# forward fill nan values (with last observed)
target.fillna(method='ffill', inplace=True)
print(target.isna().sum())

# net imports
net_imports = pd.read_csv(path_data + r'\net_position (1).csv', sep=',')
print(net_imports.shape)
# more observations
print(net_imports.columns)
print(net_imports.dtypes)
# covert time stamp to ts (UTC?)
net_imports['datetime'] = pd.to_datetime(net_imports['timestamp'], utc=True)
#net_imports['ch'] = net_imports['datetime'].diff()
#net_imports['ch'].describe()
#print(net_imports['ch'].unique())
# the same as for the target
#ch1 = net_imports[net_imports.ch.isin(['0 days 02:00:00'])][['timestamp','datetime','ch']]
# exactly the same observation
# the first observations are hourly and after that they start getting in 15 minutes, the datetime does not work!!
# map by date
# datetime is not correct when the 15-minute intervals start!!
# check na
print(net_imports.isna().sum())
# no missing values

# merge by timestamp, not datetime
target1 = target.merge(net_imports[['timestamp','net_position']], how='left', on=['timestamp']).reset_index(drop=True)
print(target1.dtypes)
print(target1.isna().sum())
# no missings 

# sum all production types and the next imports
# drop still missing values
cols = ['Biomass', 'Fossil Brown coal/Lignite', 'Fossil Gas',
       'Fossil Hard coal', 'Hydro Pumped Storage',
       'Hydro Run-of-river and poundage', 'Hydro Water Reservoir', 'Nuclear',
       'Solar', 'Waste', 'Wind Onshore','net_position']
target1['supply'] = target1[cols].sum(axis=1)
# plot series
plt.figure(figsize=(15,15))
target1[['datetime','supply']].plot(x='datetime', y='supply')
plt.show()
# looks cyclical and with a trend

# run tests on the target
# ADF - H0: unit root (non-stationary)
adf_test = adfuller(target1['supply'])
print(adf_test)
# results suggest that we can reject H0 meaning that data is stationary

# ACF
plt.figure(figsize=(15,15))
plot_acf(target1['supply'], lags=100)
plt.show()
# PACF
plt.figure(figsize=(15,15))
plot_pacf(target1['supply'], lags=100)
plt.show()
# suggests that we need at least AR(1), plus we have cyclical component at around 24 hours

# import explanatory data
# weather
weather = pd.read_csv(path_data + r'\other\weather_bg_total.csv', sep=',')
# map by timestamp
target1['timestamp_t'] = target1['timestamp'].str[:-6]
target1 = target1.merge(weather,how='left',left_on=['timestamp_t'], right_on=['timestamp']).reset_index(drop=True)
# new one is datetime_x
# drop all redundant time columns
target1.drop(['timestamp_y','datetime_y'],axis=1,inplace=True)
# rename
target1.rename(columns={'timestamp_x':'timestamp','datetime_x':'datetime'}, inplace=True)
# check missing values
print(target1.isna().sum())
# fill in with previous
target1.fillna(method='ffill', inplace=True)

# working days
work_days = pd.read_csv(path_data + r'\other\days_off_bg_2022-01-01_2026-12-31.csv', sep=',')
# merge as with the weather
target1['date1'] = target1['timestamp'].str[:-15]
target1 = target1.merge(work_days,how='left',left_on=['date1'], right_on=['date']).reset_index(drop=True)
# drop date and day_name; for the otehr 2 put 0s for the nans
target1.drop(['date','day_name'],axis=1,inplace=True)
print(target1.isna().sum())
# only these two columns have missing values, therefore do it for the full data set
target1.fillna(0, inplace=True)

# unavailability
# create only dummy variables to indicate the maintenance and the outages
unavail_prod = pd.read_csv(path_data + r'\unavailability_production_units (1).csv', sep=',')
unavail_gen = pd.read_csv(path_data + r'\unavailability_generation_units (1).csv', sep=',')

# we need to reshape these
# start and end to utc
# all are planned maintenance, so we can use only the dates
# data is hourly
unavail_prod_t = unavail_prod[['start','end']].copy()
unavail_prod_t['start_utc'] = pd.to_datetime(unavail_prod_t['start'], utc=True)
unavail_prod_t['end_utc'] = pd.to_datetime(unavail_prod_t['end'], utc=True)

# check the dates in a loop
# one data set for all dummies related to unavailability
unavailability = pd.DataFrame(target1['datetime'])
unavailability['prod_maint'] = 0
unavailability['gen_maint'] = 0
unavailability['gen_outages'] = 0
# loop over dates
# production maintenance
for i in list(range(unavailability.shape[0])):
    #print(i)
    for j in list(range(unavail_prod_t.shape[0])):
        if (unavailability.iloc[i,0] >= unavail_prod_t.iloc[j,2]) and (unavailability.iloc[i,0] <= unavail_prod_t.iloc[j,3]):
            unavailability.iloc[i,1] = 1
        else:
            continue
# takes time, not optimized
# count by type
print(unavailability.groupby(['prod_maint']).count())

# generation maintenance and outages
# take into account the type! 
# these will be hardcoded and need to be checked for new type!!

print(unavail_gen['businesstype'].unique())
unavail_gen_t = unavail_gen[['start','end','businesstype']].copy()
unavail_gen_t['start_utc'] = pd.to_datetime(unavail_gen_t['start'], utc=True)
unavail_gen_t['end_utc'] = pd.to_datetime(unavail_gen_t['end'], utc=True)
'''
# loop over dates (will take much more time)
for i in list(range(unavailability.shape[0])):
    #print(i)
    for j in list(range(unavail_gen_t.shape[0])):
        if (unavailability.iloc[i,0] >= unavail_gen_t.iloc[j,3]) and (unavailability.iloc[i,0] <= unavail_gen_t.iloc[j,4]):
            if unavail_gen_t.iloc[j,2] == 'Planned maintenance':
                unavailability.iloc[i,2] = 1
            elif unavail_gen_t.iloc[j,2] == 'Unplanned outage':
                unavailability.iloc[i,3] = 1
            else:
                print('Not in the known types.')
        else:
            continue
'''

# try alternative code (optimized in Copilot)
unavailability_test = unavailability.copy()
u_time = unavailability.iloc[:, 0].values[:, None]  # (n,1)
g_start = unavail_gen_t.iloc[:, 3].values           # (m,)
g_end = unavail_gen_t.iloc[:, 4].values             # (m,)
g_type = unavail_gen_t.iloc[:, 2].values            # (m,)

# Create matrix of matches
mask = (u_time >= g_start) & (u_time <= g_end)  # shape (n, m)

# Apply conditions
planned_mask = mask & (g_type == 'Planned maintenance')
unplanned_mask = mask & (g_type == 'Unplanned outage')

# Reduce across generators
unavailability_test.iloc[:, 2] = planned_mask.any(axis=1).astype(int)
unavailability_test.iloc[:, 3] = unplanned_mask.any(axis=1).astype(int)

# seems like it works

# count by type
print(unavailability_test.groupby(['gen_maint']).count())
print(unavailability_test.groupby(['gen_outages']).count())

# merge with main file
target1 = target1.merge(unavailability_test, how='left', on=['datetime'])

# check missing files
print(target1.isna().sum())
# no missing values

# datetime index
target_w = target1.copy()
# set index to 1h
target_w.index = target_w['datetime']
target_wf = target_w.asfreq('60t')
print(target_wf.index.freq)
print(target_wf.isna().sum())
# there is one missing value introduced due to the frequency set
# fill in with previous
target_wf.fillna(method='ffill', inplace=True)

# train test split
# start train at 20.02.2024 (?) until 30.09.2025
# test from 01.10.2025 until end of the series
target_wf['train'] = 0
target_wf.loc[target_wf.date1 < '2025-10-01','train'] = 1
print(target_wf.groupby(['train'])['datetime'].count())
# this does not result in the correct split, because we have still 3 observations from 30.09

# split 
train = target_wf[target_wf.train == 1].copy()
test = target_wf[target_wf.train == 0].copy()

# normalize
norm_cols = ['Biomass', 'Fossil Brown coal/Lignite', 'Fossil Gas',
       'Fossil Hard coal', 'Hydro Pumped Storage',
       'Hydro Run-of-river and poundage', 'Hydro Water Reservoir', 'Nuclear',
       'Solar', 'Waste', 'Wind Onshore', 'net_position', 'supply',
       'temperature_2m', 'wind_speed_10m', 'wind_speed_100m',
       'wind_direction_100m', 'shortwave_radiation',
       'direct_normal_irradiance', 'cloud_cover', 'precipitation',
       'relative_humidity_2m']
train_norm = train.copy()
test_norm = test.copy()
train_norm_f = train_norm[norm_cols].apply(lambda x: (x - x.mean()) / x.std())
test_norm_f = test_norm[norm_cols].apply(lambda x: (x - x.mean()) / x.std())
# include dummies
# train
train_norm_f['is_weekend'] = train_norm['is_weekend']
train_norm_f['is_holiday'] = train_norm['is_holiday']
train_norm_f['prod_maint'] = train_norm['prod_maint']
train_norm_f['gen_maint'] = train_norm['gen_maint']
train_norm_f['gen_outages'] = train_norm['gen_outages']
# test
test_norm_f['is_weekend'] = test_norm['is_weekend']
test_norm_f['is_holiday'] = test_norm['is_holiday']
test_norm_f['prod_maint'] = test_norm['prod_maint']
test_norm_f['gen_maint'] = test_norm['gen_maint']
test_norm_f['gen_outages'] = test_norm['gen_outages']


# train models
# set to X and y
x_cols = ['temperature_2m', 'wind_speed_10m', 'wind_speed_100m',
          'wind_direction_100m', 'shortwave_radiation',
          'direct_normal_irradiance', 'cloud_cover', 'precipitation',
          'relative_humidity_2m', 'is_weekend', 'is_holiday', 'prod_maint',
          'gen_maint', 'gen_outages']

X_train = train_norm_f[x_cols].copy()
y_train = train_norm_f['supply'].copy()

# run lasso
model1_lasso = linear_model.Lasso(alpha=0.01)
model1_lasso.fit(X_train,y_train) 
coefs_lasso = model1_lasso.coef_
# predict lasso
model1_predict = model1_lasso.predict(X_train)
tt = pd.DataFrame(y_train)
tt['pred'] = model1_predict
# plot
plt.figure(figsize=(15,15))
tt.plot()
plt.show()
# works with alpha around 0.01 
# score
model1_lasso.score(X_train,y_train) 
# around 0.25, very low

# run ridge
model2_ridge = linear_model.Ridge(alpha=1)
model2_ridge.fit(X_train,y_train) 
coefs_ridge = model2_ridge.coef_
# predict ridge
model2_predict = model2_ridge.predict(X_train)
tt = pd.DataFrame(y_train)
tt['pred'] = model2_predict
# plot
plt.figure(figsize=(15,15))
tt.plot()
plt.show()
# the same with all parameters
# score
model2_ridge.score(X_train,y_train) 
# only slightly better, around 0.26

# run elastic net
model3_elastic = linear_model.ElasticNet(alpha=0.01, random_state=0)
model3_elastic.fit(X_train,y_train) 
coefs_elastic = model3_elastic.coef_
# predict elastic net
model3_predict = model3_elastic.predict(X_train)
tt = pd.DataFrame(y_train)
tt['pred'] = model3_predict
# plot
plt.figure(figsize=(15,15))
tt.plot()
plt.show()
# the same with all parameters
# score
model3_elastic.score(X_train,y_train) 
# similar to the other two, around 0.25

# run decision tree
model4_tree = DecisionTreeRegressor(random_state=0, max_depth=10, min_samples_split=30, min_samples_leaf=30)
cross_val_score(model4_tree, X_train, y_train, cv=10)
model4_tree.fit(X_train,y_train) 
model4_tree.get_depth()
model4_tree.get_n_leaves()
# 382
# predict decision tree
model4_predict = model4_tree.predict(X_train)
tt = pd.DataFrame(y_train)
tt['pred'] = model4_predict
# plot
plt.figure(figsize=(15,15))
tt.plot()
plt.show()
# score
model4_tree.score(X_train,y_train) 
# around 0.50

# run random forest 
model5_forest = RandomForestRegressor(max_depth=10, min_samples_split=30, min_samples_leaf=30, random_state=0)
model5_forest.fit(X_train,y_train) 
model5_forest.get_params()
# predict random forest
model5_predict = model5_forest.predict(X_train)
tt = pd.DataFrame(y_train)
tt['pred'] = model5_predict
# plot
plt.figure(figsize=(15,15))
tt.plot()
plt.show()
# score
model5_forest.score(X_train,y_train) 
# around 0.53

# run gradient boosting
model6_boosting = GradientBoostingRegressor(min_samples_split=30, min_samples_leaf=30, max_depth=10, random_state=0)
model6_boosting.fit(X_train,y_train)
# predict decision tree
model6_predict = model6_boosting.predict(X_train)
tt = pd.DataFrame(y_train)
tt['pred'] = model6_predict
# plot
plt.figure(figsize=(15,15))
tt.plot()
plt.show()
# score
model6_boosting.score(X_train,y_train) 
# around 0.75

# test
X_test = test_norm_f[x_cols].copy()
y_test = test_norm_f['supply'].copy()
# model1
model1_pred_f = model1_lasso.predict(X_test)
model1_lasso.score(X_test,y_test) 
# 0.09
# model2
model2_pred_f = model2_ridge.predict(X_test)
model2_ridge.score(X_test,y_test) 
# 0.10
# model3
model3_pred_f = model3_elastic.predict(X_test)
model3_elastic.score(X_test,y_test) 
# 0.10
# model4
model4_pred_f = model4_tree.predict(X_test)
model4_tree.score(X_test,y_test) 
# -0.08
# model5
model5_pred_f = model5_forest.predict(X_test)
model5_forest.score(X_test,y_test) 
# -0.009
# model6
model6_pred_f = model6_boosting.predict(X_test)
model6_boosting.score(X_test,y_test) 
# -0.04

# merge all
test_pred_all = pd.DataFrame(y_test).copy()
test_pred_all['naive'] = y_train.iloc[-1]
test_pred_all['lasso'] = model1_pred_f
test_pred_all['ridge'] = model2_pred_f
test_pred_all['elastic_net'] = model3_pred_f
test_pred_all['decision_tree'] = model4_pred_f
test_pred_all['random_forest'] = model5_pred_f
test_pred_all['gradient_boosting'] = model6_pred_f

# plot
plt.figure(figsize=(15,15))
ax = test_pred_all.plot()
ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.2), ncol=3)
#plt.tight_layout()
plt.subplots_adjust(bottom=0.2)
plt.savefig(path_results + r'\test_pred_all_supply.pdf', bbox_inches='tight', dpi=300)
plt.show()

# Apply seaborn styling
sns.set_theme(style="whitegrid")
plt.figure(figsize=(15, 20))
ax = test_pred_all.plot()
plt.legend(loc='upper center', bbox_to_anchor=(0.5, -0.2), ncol=3)
plt.subplots_adjust(bottom=0.15)
plt.savefig(path_results + r'\test_pred_all_supply_sns.pdf', bbox_inches='tight', dpi=300)
plt.show()

# plot the full time series
sns.set_theme(style="whitegrid")
plt.figure(figsize=(20, 20))
ax = target_wf['supply'].plot()
#plt.legend(loc='upper center')
#plt.subplots_adjust(bottom=0.15)
plt.savefig(path_results + r'\supply_sns.pdf', bbox_inches='tight', dpi=300)
plt.show()

# metrics
metrics = pd.DataFrame(0.0, index=range(4), columns=range(7))
metrics.index = ['mse','rmse','mae','mape']
metrics.columns = test_pred_all.columns[1:]
# calculate errors
for i in list(range(7)):
    print(i, i+1)
    metrics.iloc[0,i] = mean_squared_error(test_pred_all['supply'], test_pred_all.iloc[:,(i+1)])   
    metrics.iloc[1,i] = root_mean_squared_error(test_pred_all['supply'], test_pred_all.iloc[:,(i+1)])   
    metrics.iloc[2,i] = mean_absolute_error(test_pred_all['supply'], test_pred_all.iloc[:,(i+1)])   
    metrics.iloc[3,i] = mean_absolute_percentage_error(test_pred_all['supply'], test_pred_all.iloc[:,(i+1)])   


# save table
metrics.round(4).to_html(path_results + r'\metrics_table.html') 

# plot and save as pdf
fig = plt.figure(figsize=(10, 3))
ax = fig.add_axes([0, 0, 1, 1])  
ax.axis('off')
table = ax.table(
    cellText=metrics.values.round(4),
    colLabels=metrics.columns,
    rowLabels=metrics.index,   
    loc='center'
)
table.auto_set_font_size(False)
table.set_fontsize(10)
table.scale(1, 1.5)
# Save as PDF
plt.savefig(path_results + r'\metrics_table.pdf', bbox_inches='tight')
plt.close()


'''
# decompose (full data set)
# this one needs to have a time index
decomp = seasonal_decompose(target_wf['supply'], model='additive')
# check results
plt.figure(figsize=(15,15))
decomp.plot()
plt.show()

# check residuals
plt.figure(figsize=(15,15))
plot_acf(decomp.resid, lags=100)
plt.show()
# PACF
plt.figure(figsize=(15,15))
plot_pacf(decomp.resid, lags=100)
plt.show()
# no signal in the residuals

# check auto arima
arima_test = auto_arima(y=target_wf['supply'],start_p=1, start_q=1)
arima_test.get_params()
# no seasonal component? no trend?
'''














