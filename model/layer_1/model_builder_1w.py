"""
Layer 1 — ПЪЛЕН pipeline (1 СЕДМИЦА, 168ч): рязане на features + 4 модела + диагностика + избор + финална графика.
Огледало на pipeline_full_l1.py (24ч), адаптирано за хоризонт 168ч.

Разлики спрямо 24ч:
  • Бенчмарк = САМО naive(lag168). ЕСО НЕ участва (ЕСО е day-ahead, не 7-дневна прогноза).
  • Метеото е lag168 proxy (миналоседмичен актуал); load лагове ≥168ч.
  • Точни линейни комбинации за рязане: diff_168_336.
  • HAC/Newey-West с 168 лага (седмична автокорелация); диагностика по седмична структура.
  • По-дълъг train (актуалното метео дава пълна история) → плъзгащ 730-дн прозорец.

Рязане на features:
  • махаме diff_168_336 (точна линейна комбинация → rank-deficiency)
  • махаме feature АКО: статистически НЕзначим (OLS+HAC p≥0.05)  И  |corr(феат→товар)| < 0.20

Модели: Ridge, Lasso, ElasticNet, XGBoost.  Бенчмарк: naive (предната седмица = lag168).
Изходи (PNG, LAYER1/results/figures/1week/):
  1 pipeline_metrics.png        — train/test MAE + overfit + RMSE/MAPE/R²/adjR²/bias + vs naive
  2 pipeline_significant.png    — значими/важни features по модел
  3 pipeline_corr_<модел>.png   — corr→таргет + feature×feature (×4)
  4 pipeline_diagnostics.png    — значимост (DM vs naive) + грешки (ACF/Ljung-Box/ARCH/JB/ADF/KPSS)
  5 pipeline_intervals.png      — 90% Mondrian покритие/ширина/Winkler
  6 pipeline_selection.png      — композитен резултат (MAPE↓ + overfit↓ + Winkler↓) → избран модел
  7 pipeline_learning_curve.png — train vs test MAE спрямо обема обучение
  8 pipeline_final_<модел>.png  — scatter(adjR²) + хистограма грешки + actual/predicted + 90% Mondrian

Тест: плъзгащ ~730д, 15-дн блокове от 2025-10-01 (вкл. зима).
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from scipy import stats as st
import statsmodels.api as sm
from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch
from statsmodels.stats.stattools import jarque_bera
from statsmodels.tsa.stattools import adfuller, kpss
from sklearn.linear_model import RidgeCV, LassoCV, ElasticNetCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBRegressor
import warnings; warnings.filterwarnings("ignore")

# Make the shared tools/ dir importable (for upload_s3) from model/layer_1/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from upload_s3 import read_csv, upload  # noqa: E402

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

DO_UPLOAD = True  # always persist; backend (s3/local) chosen in upload_s3
LOCAL = "Europe/Sofia"
H = 168                                                          # хоризонт (часа)
# PNG-овете отиват тук локално, после се качват в S3 под data/results/1week/.
FIG = Path(__file__).resolve().parents[1]/"results"/"1week"; FIG.mkdir(parents=True, exist_ok=True)
FEATURES_KEY = "data/processed/features_1week_long.csv"


def rl(key):
    d = read_csv(key, index_col=0); d.index = pd.to_datetime(d.index, utc=True).tz_convert(LOCAL); return d


print(f"features: {FEATURES_KEY}")
F = rl(FEATURES_KEY)
F["naive"] = F["lag168"]                                         # бенчмарк = предната седмица
all40 = [c for c in F.columns if c not in ("load_actual_mw", "naive")]
TEST0 = pd.Timestamp("2025-10-01", tz=LOCAL); TEST_DAYS = 15; CAL_DAYS = 30
TRAIN_LEN = pd.Timedelta(days=730); end = F.index.max(); NWLAG = 168
tscv = TimeSeriesSplit(4)
XGB = dict(n_estimators=500, max_depth=3, learning_rate=0.03, subsample=0.7, colsample_bytree=0.7,
           min_child_weight=20, reg_lambda=5.0, gamma=1.0, tree_method="hist", n_jobs=-1, random_state=42)
LIN = {"Ridge", "Lasso", "ElasticNet"}


def mk(kind):
    if kind == "Ridge": return RidgeCV(alphas=np.logspace(-2, 4, 13))
    if kind == "Lasso": return LassoCV(cv=tscv, max_iter=8000, tol=1e-3, n_jobs=-1, random_state=0)
    if kind == "ElasticNet": return ElasticNetCV(l1_ratio=[.1, .5, .7, .9, .95, .99], cv=tscv, max_iter=8000, tol=1e-3, n_jobs=-1, random_state=0)
    if kind == "XGBoost": return XGBRegressor(**XGB)


# ── 0) РЯЗАНЕ НА FEATURES ──
base = [c for c in all40 if c not in ("diff_168_336",)]                            # без точната комбинация
Dall = F[base+["load_actual_mw"]].dropna(); yt = Dall["load_actual_mw"].values
if len(Dall) == 0:                                                                  # няма пълни редове → твърде къса история
    raise SystemExit(
        "Няма НИТО един пълен ред във features_1week_long (всички редове съдържат NaN).\n"
        "Най-вероятно lag8760 (1г warmup) надхвърля наличната история — актуалното метео\n"
        "(weather_bg_total) е по-късо от товара. Презапиши го с по-дълъг прозорец, напр.:\n"
        "  python tools/scrapers/scrape_weather_bulgaria.py 2022-09-01 <today>\n"
        "после: transform_1_week_forecast_local_time.py → feature_builder_1w.py (S3 по подразбиране).")
Z = (Dall[base]-Dall[base].mean())/Dall[base].std()
ols0 = sm.OLS(yt, sm.add_constant(Z.values)).fit(cov_type="HAC", cov_kwds={"maxlags": NWLAG})
pval = pd.Series(ols0.pvalues[1:], index=base)
corr = Dall[base].corrwith(Dall["load_actual_mw"])
drop = [c for c in base if (pval[c] >= 0.05) and (abs(corr[c]) < 0.20)]            # незначим И |corr|<0.2
cols = [c for c in base if c not in drop]
print(f"РЯЗАНЕ: 40 → без diff (39) → махнати по критерий [{len(drop)}]: {', '.join(drop)}")
print(f"ОСТАВАТ {len(cols)} features\n")


# ── 1) WFO 4 модела ──
def wfo(kind, columns):
    p = pd.Series(np.nan, index=F.index); tr_maes = []; ts = TEST0
    while ts <= end:
        te_end = min(ts+pd.Timedelta(days=TEST_DAYS), end+pd.Timedelta(hours=1))
        tr = F[(F.index >= ts-TRAIN_LEN) & (F.index < ts)].dropna(subset=columns+["load_actual_mw"])
        te = F[(F.index >= ts) & (F.index < te_end)].dropna(subset=columns+["load_actual_mw"])
        if len(tr) < 4000 or len(te) == 0: ts = te_end; continue
        ytr = tr["load_actual_mw"].values; m = mk(kind)
        if kind in LIN:
            sc = StandardScaler().fit(tr[columns]); m.fit(sc.transform(tr[columns]), ytr)
            ptr = m.predict(sc.transform(tr[columns])); p.loc[te.index] = m.predict(sc.transform(te[columns]))
        else:
            m.fit(tr[columns], ytr); ptr = m.predict(tr[columns]); p.loc[te.index] = m.predict(te[columns])
        tr_maes.append(np.abs(ytr-ptr).mean()); ts = te_end
    return p, float(np.mean(tr_maes))


MODELS = ["Ridge", "Lasso", "ElasticNet", "XGBoost"]
print("WFO 4 модела (168ч)..."); preds = {}; trmae = {}
for k in MODELS: preds[k], trmae[k] = wfo(k, cols); print(f"  {k} ✓")

D = pd.DataFrame({"y": F["load_actual_mw"], **{k: preds[k] for k in MODELS}, "naive": F["naive"]}).dropna()
y = D["y"].values; n = len(D); hour = D.index.hour.values
def met(p, k):
    e = p-y; R2 = 1-(e**2).sum()/((y-y.mean())**2).sum()
    return dict(MAE=np.abs(e).mean(), RMSE=np.sqrt((e**2).mean()), MAPE=(np.abs(e)/y).mean()*100,
                R2=R2, adjR2=1-(1-R2)*(n-1)/(n-k-1), bias=e.mean())
mm = {nm: met(D[nm].values, len(cols) if nm in MODELS else 0) for nm in MODELS+["naive"]}
nv_mae = mm["naive"]["MAE"]


def save_table(df, path, title, figsize):
    fig, ax = plt.subplots(figsize=figsize); ax.axis("off")
    tb = ax.table(cellText=df.values, colLabels=df.columns, rowLabels=df.index, loc="center", cellLoc="center")
    tb.auto_set_font_size(False); tb.set_fontsize(9); tb.scale(1, 1.5)
    for (r, c), cell in tb.get_celld().items():
        if r == 0 or c == -1: cell.set_facecolor("#1e293b"); cell.set_text_props(color="white", weight="bold")
        elif r % 2 == 0: cell.set_facecolor("#f1f5f9")
    ax.set_title(title, fontsize=12, weight="bold", pad=12)
    plt.tight_layout(); plt.savefig(path, dpi=150, bbox_inches="tight"); plt.close()


# 1) метрики (+ peak-hour: грешка в дневния максимум на товара)
Dd = D.copy(); Dd["date"] = Dd.index.date
peak_ix = Dd.groupby("date")["y"].idxmax()                     # часът на дневния пик (по реален товар)
P = D.loc[peak_ix]; yp = P["y"].values
T = {}
for nm in MODELS+["naive"]:
    m = mm[nm]; tr = trmae.get(nm)
    pe = np.abs(P[nm].values-yp)
    T[nm] = {"train MAE": f"{tr:.1f}" if tr else "—", "test MAE": f"{m['MAE']:.1f}",
             "overfit": f"{m['MAE']/tr:.2f}" if tr else "—", "RMSE": f"{m['RMSE']:.1f}",
             "MAPE%": f"{m['MAPE']:.2f}", "peak MAE": f"{pe.mean():.1f}", "peak MAPE%": f"{(pe/yp).mean()*100:.2f}",
             "R²": f"{m['R2']:.4f}", "adjR²": f"{m['adjR2']:.4f}", "bias": f"{m['bias']:+.1f}",
             "vs naive": f"{(nv_mae-m['MAE'])/nv_mae*100:+.1f}%"}
save_table(pd.DataFrame(T).T, FIG/"pipeline_metrics.png",
           f"L1 1седмица 168ч ({len(cols)} ft) — 4 модела vs naive (тест {D.index.min().date()}→{D.index.max().date()}, n={n}) · peak=дневен пик", (15, 4))
print("1) метрики (+ peak) ✓")

# ── значимост/importance/корелации (пълни данни, орязан сет) ──
Dc = F[cols+["load_actual_mw"]].dropna(); yc = Dc["load_actual_mw"].values
Zc = (Dc[cols]-Dc[cols].mean())/Dc[cols].std()
olsc = sm.OLS(yc, sm.add_constant(Zc.values)).fit(cov_type="HAC", cov_kwds={"maxlags": NWLAG})
pvc = pd.Series(olsc.pvalues[1:], index=cols); sig = set(pvc.index[pvc < 0.05])
sc = StandardScaler().fit(Dc[cols])
las = LassoCV(cv=tscv, max_iter=8000, tol=1e-3, n_jobs=-1, random_state=0).fit(sc.transform(Dc[cols]), yc)
ela = ElasticNetCV(l1_ratio=[.1, .5, .7, .9, .95, .99], cv=tscv, max_iter=8000, tol=1e-3, n_jobs=-1, random_state=0).fit(sc.transform(Dc[cols]), yc)
xgf = XGBRegressor(**XGB).fit(Dc[cols], yc); gimp = pd.Series(xgf.feature_importances_, index=cols)
sel = {"Ridge": sig, "Lasso": set(np.array(cols)[np.abs(las.coef_) > 1e-6]),
       "ElasticNet": set(np.array(cols)[np.abs(ela.coef_) > 1e-6]), "XGBoost": set(gimp.index[gimp > 0.005])}
corrc = Dc[cols].corrwith(Dc["load_actual_mw"])

# 2) значими features
order = corrc.abs().sort_values(ascending=False).index
G = pd.DataFrame(index=order); G["corr"] = corrc.reindex(order).round(2)
for k in MODELS: G[k] = ["✓" if f in sel[k] else "" for f in order]
G.index.name = "feature"
save_table(G.reset_index().set_index("feature"), FIG/"pipeline_significant.png",
           "Значими/важни features по модел (Ridge:OLS p<0.05 · Lasso/Elastic:≠0 · XGB:imp>0.5%)", (8, max(6, len(cols)*0.34)))
print("2) значими features ✓")

# 3) per-model корелации
def blk(c): return ("LOAD" if c.startswith(("lag", "roll", "diff")) else
                    "CAL" if any(c.startswith(p) for p in ("sin", "cos", "is_", "hol", "pre_", "post_", "bridge")) else "WX")
BCOL = {"LOAD": "#2563eb", "WX": "#16a34a", "CAL": "#d97706"}
for k in MODELS:
    feats = sorted([f for f in cols if f in sel[k]], key=lambda c: ({"LOAD": 0, "WX": 1, "CAL": 2}[blk(c)], c))
    if len(feats) < 2: continue
    cc = Dc[feats].corrwith(Dc["load_actual_mw"]); CM = Dc[feats].corr()
    fig = plt.figure(figsize=(15, max(6, len(feats)*0.34))); gs = fig.add_gridspec(1, 2, width_ratios=[1, 2.1], wspace=0.05)
    ax1 = fig.add_subplot(gs[0]); ss = cc.reindex(cc.abs().sort_values().index)
    ax1.barh(range(len(ss)), ss.values, color=[BCOL[blk(c)] for c in ss.index]); ax1.axvline(0, color="k", lw=.6)
    ax1.set_yticks(range(len(ss))); ax1.set_yticklabels(ss.index, fontsize=7); ax1.set_title(f"{k}: corr → товар")
    ax2 = fig.add_subplot(gs[1]); im = ax2.imshow(CM.values, cmap="RdBu_r", norm=TwoSlopeNorm(vmin=-1, vcenter=0, vmax=1))
    fig.colorbar(im, ax=ax2, fraction=.046, pad=.04)
    ax2.set_xticks(range(len(feats))); ax2.set_xticklabels(feats, rotation=90, fontsize=6)
    ax2.set_yticks(range(len(feats))); ax2.set_yticklabels(feats, fontsize=6)
    for b in [i for i in range(1, len(feats)) if blk(feats[i]) != blk(feats[i-1])]:
        ax2.axhline(b-.5, color="k", lw=1); ax2.axvline(b-.5, color="k", lw=1)
    ax2.set_title(f"{k}: feature×feature ({len(feats)} ползвани)")
    fig.suptitle(f"Модел {k} — корелации (168ч)", fontsize=14, weight="bold")
    plt.savefig(FIG/f"pipeline_corr_{k}.png", dpi=150, bbox_inches="tight"); plt.close()
print("3) per-model корелации ✓")

# ── 4) ДИАГНОСТИКА: значимост (DM vs naive) + грешки ──
def nw(d, L):
    d = d-d.mean(); nn = len(d); s = d@d/nn
    for l in range(1, L+1): s += 2*(1-l/(L+1))*(d[l:]@d[:-l])/nn
    return s
def dm_p(e1, e2):
    d = np.abs(e1)-np.abs(e2); stat = d.mean()/np.sqrt(nw(d, NWLAG)/len(d)); return 2*(1-st.norm.cdf(abs(stat)))
def acf(e, l): e = e-e.mean(); return float((e[l:]@e[:-l])/(e@e))
D2 = {}
for k in MODELS:
    e = (D[k]-D["y"]).values; env = (D["naive"]-D["y"]).values
    se = np.sqrt(nw(e, NWLAG)/n); bp = 2*(1-st.norm.cdf(abs(e.mean()/se)))
    lb = acorr_ljungbox(e, lags=[H], return_df=True)["lb_pvalue"].iloc[0]
    arch = het_arch(e, nlags=48)[1]; jb = jarque_bera(e)[1]
    adf = adfuller(e, autolag="AIC")[1]; kp = kpss(e, nlags="auto")[1]
    D2[k] = {"DM vs naive": f"{dm_p(e, env):.1e}", "bias p(HAC)": f"{bp:.2f}",
             "Ljung-Box(168)": f"{lb:.1e}", "ACF1": f"{acf(e,1):.2f}", "ACF168": f"{acf(e,H):.2f}",
             "ARCH p": f"{arch:.1e}", "Norm(JB) p": f"{jb:.1e}", "ADF p": f"{adf:.2f}", "KPSS p": f"{kp:.2f}"}
save_table(pd.DataFrame(D2).T, FIG/"pipeline_diagnostics.png",
           "Диагностика 168ч: значимост (DM vs naive, p<0.05=значимо по-добър) + грешки (Ljung/ACF=автокор · ARCH=хетероскед · JB=норм · ADF/KPSS=стац)", (14, 3.5))
print("4) диагностика ✓")

# ── CONFORMAL (Mondrian по час, всички модели) → Winkler (ПРЕДИ избора) ──
def cq(r, a):
    r = np.sort(np.abs(r)); m = len(r)
    return r[min(int(np.ceil((m+1)*(1-a))), m)-1] if m else np.nan


def conformal_pass(kind):
    rows = []; ts = TEST0
    while ts <= end:
        te_end = min(ts+pd.Timedelta(days=TEST_DAYS), end+pd.Timedelta(hours=1)); cal0 = ts-pd.Timedelta(days=CAL_DAYS)
        tr = F[(F.index >= cal0-TRAIN_LEN) & (F.index < cal0)].dropna(subset=cols+["load_actual_mw"])
        ca = F[(F.index >= cal0) & (F.index < ts)].dropna(subset=cols+["load_actual_mw"])
        te = F[(F.index >= ts) & (F.index < te_end)].dropna(subset=cols+["load_actual_mw"])
        if len(tr) < 4000 or len(ca) < 200 or len(te) == 0: ts = te_end; continue
        m = mk(kind)
        if kind in LIN:
            scl = StandardScaler().fit(tr[cols]); m.fit(scl.transform(tr[cols]), tr["load_actual_mw"].values)
            cp = m.predict(scl.transform(ca[cols])); tp = m.predict(scl.transform(te[cols]))
        else:
            m.fit(tr[cols], tr["load_actual_mw"].values); cp = m.predict(ca[cols]); tp = m.predict(te[cols])
        cr = ca["load_actual_mw"].values-cp; chh = ca.index.hour.values
        qh = {hh: cq(cr[chh == hh], 0.10) for hh in range(24)}; qg = cq(cr, 0.10); thh = te.index.hour.values
        for i, ix in enumerate(te.index):
            w = qh.get(thh[i], qg); w = qg if not np.isfinite(w) else w
            rows.append((ix, te["load_actual_mw"].values[i], tp[i], w))
        ts = te_end
    Rd = pd.DataFrame(rows, columns=["ts", "y", "p", "w"]).set_index("ts")
    Rd["lo"] = Rd["p"]-Rd["w"]; Rd["hi"] = Rd["p"]+Rd["w"]; Rd["in"] = (Rd["y"] >= Rd["lo"]) & (Rd["y"] <= Rd["hi"])
    return Rd


def winkler(Rd, alpha=0.10):
    yv, lo, hi = Rd["y"].values, Rd["lo"].values, Rd["hi"].values
    w = (hi-lo) + np.where(yv < lo, (2/alpha)*(lo-yv), 0.0) + np.where(yv > hi, (2/alpha)*(yv-hi), 0.0)
    return w.mean()


print("conformal за всички модели..."); Rall = {k: conformal_pass(k) for k in MODELS}
wink = {k: winkler(Rall[k]) for k in MODELS}
IT = {k: {"покритие %": f"{Rall[k]['in'].mean()*100:.1f}", "ср.полу-ширина ±MW": f"{Rall[k]['w'].mean():.0f}",
          "Winkler score": f"{wink[k]:.0f}"} for k in MODELS}
save_table(pd.DataFrame(IT).T, FIG/"pipeline_intervals.png",
           "Качество на 90% Mondrian интервали (цел покритие ~90% · Winkler↓ = по-добре)", (10, 3))
print("конформал/Winkler ✓")

# ── 5) ИЗБОР: НАЙ-НИСКО MAPE + НАЙ-НИСЪК OVERFIT + НАЙ-ДОБЪР WINKLER (равни тегла, норм.) ──
sc_df = pd.DataFrame({k: {"MAPE": mm[k]["MAPE"], "overfit": mm[k]["MAE"]/trmae[k], "Winkler": wink[k]} for k in MODELS}).T
def nrm(x):
    rng = x.max()-x.min(); return (x.max()-x)/rng if rng > 0 else pd.Series(1.0, index=x.index)   # по-ниско = по-добре
sc_df["s_MAPE"] = nrm(sc_df["MAPE"]); sc_df["s_overfit"] = nrm(sc_df["overfit"]); sc_df["s_Winkler"] = nrm(sc_df["Winkler"])
sc_df["SCORE"] = sc_df[["s_MAPE", "s_overfit", "s_Winkler"]].mean(axis=1)
SELECTED = sc_df["SCORE"].idxmax()
disp = sc_df.copy()
for c in disp.columns: disp[c] = disp[c].round(3)
disp = disp.sort_values("SCORE", ascending=False)
save_table(disp.astype(str), FIG/"pipeline_selection.png",
           f"Избор: норм. MAPE↓ + overfit↓ + Winkler↓ (равни тегла) → ИЗБРАН: {SELECTED}", (13, 3))
print(f"5) избор ✓  → {SELECTED}")

# ── 5b) LEARNING CURVE на избрания (train vs test MAE спрямо обема обучение) ──
VAL_DAYS = 45
vstart = end - pd.Timedelta(days=VAL_DAYS)
VAL = F[(F.index >= vstart) & (F.index <= end)].dropna(subset=cols+["load_actual_mw"])
yval = VAL["load_actual_mw"].values
lc = []
for sz in (30, 60, 90, 150, 240, 365, 480, 560, 700):
    tr = F[(F.index >= vstart-pd.Timedelta(days=sz)) & (F.index < vstart)].dropna(subset=cols+["load_actual_mw"])
    if len(tr) < 500: continue
    ytr = tr["load_actual_mw"].values; m = mk(SELECTED)
    if SELECTED in LIN:
        scl = StandardScaler().fit(tr[cols]); m.fit(scl.transform(tr[cols]), ytr)
        trp = m.predict(scl.transform(tr[cols])); vp = m.predict(scl.transform(VAL[cols]))
    else:
        m.fit(tr[cols], ytr); trp = m.predict(tr[cols]); vp = m.predict(VAL[cols])
    lc.append((sz, np.abs(ytr-trp).mean(), np.abs(yval-vp).mean()))
LC = pd.DataFrame(lc, columns=["days", "train", "val"])
plt.figure(figsize=(10, 6))
plt.plot(LC["days"], LC["train"], "o-", color="#2563eb", lw=2, label="train MAE (in-sample)")
plt.plot(LC["days"], LC["val"], "s-", color="#dc2626", lw=2, label=f"test MAE (последни {VAL_DAYS} дни)")
plt.fill_between(LC["days"], LC["train"], LC["val"], alpha=.12, color="gray")
for _, r in LC.iterrows(): plt.annotate(f"{r['val']/r['train']:.2f}", (r["days"], (r["train"]+r["val"])/2), fontsize=7, ha="center", color="gray")
plt.xlabel("дни обучение (обем train)"); plt.ylabel("MAE (MW)")
plt.title(f"{SELECTED} (168ч) — Learning curve (train vs test) · сивото = overfit gap")
plt.legend(); plt.grid(alpha=.3)
plt.savefig(FIG/f"pipeline_learning_curve_{SELECTED}.png", dpi=150, bbox_inches="tight"); plt.close()
print(f"5b) learning curve ✓")

# ── 6) ФИНАЛНА ГРАФИКА за избрания: scatter(adjR²) + хистограма + actual/pred + Mondrian 90% ──
R = Rall[SELECTED]                                                                # вече сметнато по-горе
yy, pp = R["y"].values, R["p"].values; ee = pp-yy; nR = len(R)
r2 = 1-(ee**2).sum()/((yy-yy.mean())**2).sum(); adj = 1-(1-r2)*(nR-1)/(nR-len(cols)-1)
mae = np.abs(ee).mean(); cov = R["in"].mean()*100

fig = plt.figure(figsize=(14, 11))
# (a) scatter + линия + adjR²
ax1 = fig.add_subplot(2, 2, 1)
ax1.scatter(yy, pp, s=5, alpha=.25, color="#2563eb", edgecolors="none")
lim = [min(yy.min(), pp.min()), max(yy.max(), pp.max())]; ax1.plot(lim, lim, "k-", lw=1.2, label="y=x")
b1, b0 = np.polyfit(yy, pp, 1); ax1.plot(lim, [b0+b1*lim[0], b0+b1*lim[1]], "r--", lw=1.2, label=f"fit (накл.{b1:.2f})")
ax1.set_xlabel("Actual (MW)"); ax1.set_ylabel("Predicted (MW)"); ax1.set_aspect("equal", "box")
ax1.set_title(f"{SELECTED}: Actual vs Predicted · R²={r2:.3f} · adjR²={adj:.3f} · MAE={mae:.1f}")
ax1.legend(fontsize=8); ax1.grid(alpha=.3)
# (b) хистограма на грешките (не-нормалност → защо Mondrian)
ax2 = fig.add_subplot(2, 2, 2)
ax2.hist(ee, bins=60, color="#16a34a", alpha=.7, density=True)
xs = np.linspace(ee.min(), ee.max(), 200); ax2.plot(xs, st.norm.pdf(xs, ee.mean(), ee.std()), "r-", lw=1.5, label="нормално (за справка)")
ax2.set_title(f"Разпределение на грешките · skew={st.skew(ee):+.2f} kurt={st.kurtosis(ee)+3:.1f} (не-нормално → Mondrian)")
ax2.set_xlabel("грешка (MW)"); ax2.legend(fontsize=8); ax2.grid(alpha=.3)
# (c) времеви ред actual vs predicted + 90% Mondrian лента
ax3 = fig.add_subplot(2, 1, 2)
w0 = pd.Timestamp("2026-01-12", tz=LOCAL); W = R[(R.index >= w0) & (R.index < w0+pd.Timedelta(days=21))]
ax3.fill_between(W.index, W["lo"], W["hi"], color="#fca5a5", alpha=.45, label="90% Mondrian интервал")
ax3.plot(W.index, W["p"], color="#dc2626", lw=1.1, ls="--", label="Predicted")
ax3.plot(W.index, W["y"], color="#111", lw=1.3, label="Actual")
out = W[~W["in"]]; ax3.scatter(out.index, out["y"], s=20, color="#1d4ed8", zorder=5, label=f"извън лентата")
ax3.set_title(f"{SELECTED} (168ч): Actual vs Predicted + 90% Mondrian интервал (покритие {cov:.1f}%)")
ax3.set_ylabel("товар (MW)"); ax3.legend(loc="upper right", fontsize=8); ax3.grid(alpha=.3)
fig.suptitle(f"ИЗБРАН МОДЕЛ (1 СЕДМИЦА, 168ч): {SELECTED}", fontsize=15, weight="bold")
plt.tight_layout(); plt.savefig(FIG/f"pipeline_final_{SELECTED}.png", dpi=150, bbox_inches="tight"); plt.close()
print(f"6) финална графика ✓ (Mondrian покритие {cov:.1f}%)\n\nГОТОВО. Избран модел: {SELECTED}")

if DO_UPLOAD:
    # Качи цялата папка → s3://.../data/results/1week/<png>
    upload(FIG, prefix="data/results")