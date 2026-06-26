"""
Layer 1 — ПЪЛЕН pipeline: 4 модела (Ridge/Lasso/ElasticNet/XGBoost) на 24ч прогноза.
БЕЗ ЕСО load_forecast като feature. Бенчмарки: ЕСО (day-ahead) и naive (предния ден = lag24).
Изходи (PNG, качват се в S3 под data/results/1d/):
  1) pipeline_metrics.png        — train/test MAE + overfit ratio + RMSE/MAPE/R²/adjR²/bias + vs ЕСО/naive
  2) pipeline_significant.png    — значими/важни features за всеки модел
  3) pipeline_corr_<МОДЕЛ>.png   — за ВСЕКИ модел: corr(feature→товар) + feature×feature матрица
                                    (върху feature-ите, които моделът ползва/избира)
  4) pipeline_diagnostics.png    — значимост (DM vs naive/ЕСО) + грешки (ACF/Ljung-Box/ARCH/JB/ADF/KPSS)
  5) pipeline_intervals.png      — 90% Mondrian покритие/ширина/Winkler
  6) pipeline_selection.png      — композитен резултат (MAPE↓ + overfit↓ + Winkler↓) → избран модел
  7) pipeline_learning_curve.png — train vs test MAE спрямо обема обучение
  8) pipeline_final_<модел>.png  — scatter(adjR²) + хистограма грешки + actual/predicted + 90% Mondrian
Тест: плъзгащ ~589д, 15-дн блокове от 2025-10-01 (вкл. зима). Огледало на 1-седмичния pipeline.
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

# Make the shared tools/ dir importable (for upload_s3) from model/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from upload_s3 import read_csv, upload  # noqa: E402

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

DO_UPLOAD = "--upload" in sys.argv
LOCAL = "Europe/Sofia"
# PNG-овете отиват тук локално, после се качват в S3 под data/results/1d/.
FIG = Path(__file__).parent/"results"/"1d"; FIG.mkdir(parents=True, exist_ok=True)
MASTER_KEY = "data/processed/master_hourly_long_forecasted_weather.csv"
# feature builder-ът пише ниво ИЛИ диференциран таргет — пробвай нивовия, после diff24.
FEATURES_KEYS = ["data/processed/features_1h_long.csv",
                 "data/processed/features_1h_long_diff24.csv"]


def rl(key):
    d = read_csv(key, index_col=0); d.index = pd.to_datetime(d.index, utc=True).tz_convert(LOCAL); return d


def load_features():
    for key in FEATURES_KEYS:
        try:
            print(f"features: {key}")
            return rl(key)
        except Exception:
            continue
    raise SystemExit(f"No features CSV found in S3 (tried {FEATURES_KEYS})")


F = load_features()
print(f"master: {MASTER_KEY}")
M = rl(MASTER_KEY)
cols = [c for c in F.columns if c != "load_actual_mw"]
F["eso"] = M["load_forecast_mw"].reindex(F.index)
F["naive"] = F["lag24"]                                          # предния ден
TEST0 = pd.Timestamp("2025-10-01", tz=LOCAL); TEST_DAYS = 15
TRAIN_LEN = TEST0 - pd.Timestamp("2024-02-20", tz=LOCAL); end = F.index.max()
tscv = TimeSeriesSplit(4)
XGB = dict(n_estimators=500, max_depth=3, learning_rate=0.03, subsample=0.7, colsample_bytree=0.7,
           min_child_weight=20, reg_lambda=5.0, gamma=1.0, tree_method="hist", n_jobs=-1, random_state=42)
LIN = {"Ridge", "Lasso", "ElasticNet"}


def mk(kind):
    if kind == "Ridge": return RidgeCV(alphas=np.logspace(-2, 4, 13))
    if kind == "Lasso": return LassoCV(cv=tscv, max_iter=8000, tol=1e-3, n_jobs=-1, random_state=0)
    if kind == "ElasticNet": return ElasticNetCV(l1_ratio=[.1, .5, .7, .9, .95, .99], cv=tscv, max_iter=8000, tol=1e-3, n_jobs=-1, random_state=0)
    if kind == "XGBoost": return XGBRegressor(**XGB)


def wfo(kind):
    """връща (OOS test прогнози, среден train MAE по фолд)."""
    p = pd.Series(np.nan, index=F.index); tr_maes = []; ts = TEST0
    while ts <= end:
        te_end = min(ts+pd.Timedelta(days=TEST_DAYS), end+pd.Timedelta(hours=1))
        tr = F[(F.index >= ts-TRAIN_LEN) & (F.index < ts)].dropna(subset=cols+["load_actual_mw"])
        te = F[(F.index >= ts) & (F.index < te_end)].dropna(subset=cols+["load_actual_mw"])
        if len(tr) < 4000 or len(te) == 0: ts = te_end; continue
        ytr = tr["load_actual_mw"].values; m = mk(kind)
        if kind in LIN:
            sc = StandardScaler().fit(tr[cols]); m.fit(sc.transform(tr[cols]), ytr)
            ptr = m.predict(sc.transform(tr[cols])); p.loc[te.index] = m.predict(sc.transform(te[cols]))
        else:
            m.fit(tr[cols], ytr); ptr = m.predict(tr[cols]); p.loc[te.index] = m.predict(te[cols])
        tr_maes.append(np.abs(ytr-ptr).mean()); ts = te_end
    return p, float(np.mean(tr_maes))


MODELS = ["Ridge", "Lasso", "ElasticNet", "XGBoost"]
print("WFO 4 модела..."); preds = {}; train_mae = {}
for k in MODELS:
    preds[k], train_mae[k] = wfo(k); print(f"  {k} ✓")

# ── 1) метрики (с train/test MAE + overfit ratio) ──
D = pd.DataFrame({"y": F["load_actual_mw"], **{k: preds[k] for k in MODELS},
                  "ЕСО": F["eso"], "naive": F["naive"]}).dropna()
y = D["y"].values; n = len(D)
def met(p, k):
    e = p-y; R2 = 1-(e**2).sum()/((y-y.mean())**2).sum()
    return dict(MAE=np.abs(e).mean(), RMSE=np.sqrt((e**2).mean()), MAPE=(np.abs(e)/y).mean()*100,
                R2=R2, adjR2=1-(1-R2)*(n-1)/(n-k-1), bias=e.mean())
rowsm = {name: met(D[name].values, len(cols) if name in MODELS else 0) for name in MODELS+["ЕСО", "naive"]}
eso_mae = rowsm["ЕСО"]["MAE"]; nv_mae = rowsm["naive"]["MAE"]
T = {}
for name in MODELS+["ЕСО", "naive"]:
    m = rowsm[name]; trM = train_mae.get(name)
    T[name] = {"train MAE": f"{trM:.1f}" if trM else "—",
               "test MAE": f"{m['MAE']:.1f}",
               "overfit": f"{m['MAE']/trM:.2f}" if trM else "—",
               "RMSE": f"{m['RMSE']:.1f}", "MAPE%": f"{m['MAPE']:.2f}",
               "R²": f"{m['R2']:.4f}", "adjR²": f"{m['adjR2']:.4f}", "bias": f"{m['bias']:+.1f}",
               "vs ЕСО": f"{(eso_mae-m['MAE'])/eso_mae*100:+.1f}%", "vs naive": f"{(nv_mae-m['MAE'])/nv_mae*100:+.1f}%"}
T = pd.DataFrame(T).T


def save_table(df, path, title, figsize):
    fig, ax = plt.subplots(figsize=figsize); ax.axis("off")
    tb = ax.table(cellText=df.values, colLabels=df.columns, rowLabels=df.index, loc="center", cellLoc="center")
    tb.auto_set_font_size(False); tb.set_fontsize(9); tb.scale(1, 1.5)
    for (r, c), cell in tb.get_celld().items():
        if r == 0 or c == -1: cell.set_facecolor("#1e293b"); cell.set_text_props(color="white", weight="bold")
        elif r % 2 == 0: cell.set_facecolor("#f1f5f9")
    ax.set_title(title, fontsize=13, weight="bold", pad=14)
    plt.tight_layout(); plt.savefig(path, dpi=150, bbox_inches="tight"); plt.close()


save_table(T, FIG/"pipeline_metrics.png",
           f"L1 24ч — 4 модела vs ЕСО/naive (тест {D.index.min().date()}→{D.index.max().date()}, n={n}) · overfit=test/train",
           (13, 4))
print("1) метрики (+ train/test/overfit) ✓")

# ── фит върху пълните данни за значимост/importance/корелации ──
Dall = F[cols+["load_actual_mw"]].dropna(); yt = Dall["load_actual_mw"].values
infer = [c for c in cols if c not in ("diff_24_48", "diff_24_168")]
Zi = (Dall[infer]-Dall[infer].mean())/Dall[infer].std()
ols = sm.OLS(yt, sm.add_constant(Zi.values)).fit(cov_type="HAC", cov_kwds={"maxlags": 48})
pval = pd.Series(ols.pvalues[1:], index=infer); sig_lin = set(pval.index[pval < 0.05])
sc = StandardScaler().fit(Dall[cols])
las = LassoCV(cv=tscv, max_iter=8000, tol=1e-3, n_jobs=-1, random_state=0).fit(sc.transform(Dall[cols]), yt)
ela = ElasticNetCV(l1_ratio=[.1, .5, .7, .9, .95, .99], cv=tscv, max_iter=8000, tol=1e-3, n_jobs=-1, random_state=0).fit(sc.transform(Dall[cols]), yt)
xgf = XGBRegressor(**XGB).fit(Dall[cols], yt); gimp = pd.Series(xgf.feature_importances_, index=cols)
sel = {"Ridge": sig_lin,
       "Lasso": set(np.array(cols)[np.abs(las.coef_) > 1e-6]),
       "ElasticNet": set(np.array(cols)[np.abs(ela.coef_) > 1e-6]),
       "XGBoost": set(gimp.index[gimp > 0.005])}
corr = Dall[cols].corrwith(Dall["load_actual_mw"])

# ── 2) таблица значими/важни features ──
order = corr.abs().sort_values(ascending=False).index
G = pd.DataFrame(index=order); G["corr"] = corr.reindex(order).round(2)
for k in MODELS: G[k] = ["✓" if f in sel[k] else "" for f in order]
G.index.name = "feature"
print(f"2) значими features ✓  (бр.: {dict((k, len(sel[k])) for k in MODELS)})")
save_table(G.reset_index().set_index("feature"), FIG/"pipeline_significant.png",
           "Значими/важни features по модел  (Ridge: OLS p<0.05 · Lasso/Elastic: ненулев коеф. · XGB: importance>0.5%)",
           (8, 15))


def blk(c):
    return ("LOAD" if c.startswith(("lag", "roll", "diff")) else
            "CAL" if any(c.startswith(p) for p in ("sin", "cos", "is_", "hol", "pre_", "post_", "bridge")) else "WX")
BCOL = {"LOAD": "#2563eb", "WX": "#16a34a", "CAL": "#d97706"}

# ── 3) ЗА ВСЕКИ МОДЕЛ: corr→таргет + feature×feature (върху feature-ите на модела) ──
for k in MODELS:
    feats = [f for f in cols if f in sel[k]]
    if len(feats) < 2:
        print(f"3) {k}: пропуснат (под 2 feature-а)"); continue
    feats = sorted(feats, key=lambda c: ({"LOAD": 0, "WX": 1, "CAL": 2}[blk(c)], c))   # по блок
    cc = Dall[feats].corrwith(Dall["load_actual_mw"]); CM = Dall[feats].corr()
    fig = plt.figure(figsize=(15, max(7, len(feats)*0.34)))
    gs = fig.add_gridspec(1, 2, width_ratios=[1, 2.1], wspace=0.05)
    # ляво: corr → таргет
    ax1 = fig.add_subplot(gs[0]); ss = cc.reindex(cc.abs().sort_values().index)
    ax1.barh(range(len(ss)), ss.values, color=[BCOL[blk(c)] for c in ss.index])
    ax1.set_yticks(range(len(ss))); ax1.set_yticklabels(ss.index, fontsize=7)
    ax1.axvline(0, color="k", lw=0.6); ax1.set_xlabel("corr → товар"); ax1.set_title(f"{k}: корелация с товара")
    ax1.legend([plt.Rectangle((0, 0), 1, 1, color=BCOL[b]) for b in BCOL], BCOL.keys(), fontsize=7, loc="lower right")
    # дясно: feature×feature матрица
    ax2 = fig.add_subplot(gs[1])
    im = ax2.imshow(CM.values, cmap="RdBu_r", norm=TwoSlopeNorm(vmin=-1, vcenter=0, vmax=1))
    fig.colorbar(im, ax=ax2, fraction=0.046, pad=0.04)
    ax2.set_xticks(range(len(feats))); ax2.set_xticklabels(feats, rotation=90, fontsize=6)
    ax2.set_yticks(range(len(feats))); ax2.set_yticklabels(feats, fontsize=6)
    bnd = [i for i in range(1, len(feats)) if blk(feats[i]) != blk(feats[i-1])]
    for b in bnd: ax2.axhline(b-0.5, color="k", lw=1); ax2.axvline(b-0.5, color="k", lw=1)
    ax2.set_title(f"{k}: feature × feature ({len(feats)} ползвани feature-а)")
    fig.suptitle(f"Модел {k} — корелации (LOAD | WX | CAL)", fontsize=14, weight="bold")
    plt.savefig(FIG/f"pipeline_corr_{k}.png", dpi=150, bbox_inches="tight"); plt.close()
    print(f"3) corr {k} ✓ ({len(feats)} feature-а)")

# ════════════════════════════════════════════════════════════════════════════
# Разширена диагностика/интервали/избор/финал — огледало на 1-седмичния pipeline,
# адаптирано за 24ч (бенчмарки ЕСО+naive, HAC 48 лага, хоризонт H=24).
# ════════════════════════════════════════════════════════════════════════════
H = 24; NWLAG = 48; CAL_DAYS = 30


def nw(d, L):
    d = d-d.mean(); nn = len(d); s = d@d/nn
    for l in range(1, L+1): s += 2*(1-l/(L+1))*(d[l:]@d[:-l])/nn
    return s


def dm_p(e1, e2):
    d = np.abs(e1)-np.abs(e2); stat = d.mean()/np.sqrt(nw(d, NWLAG)/len(d)); return 2*(1-st.norm.cdf(abs(stat)))


def acf(e, l): e = e-e.mean(); return float((e[l:]@e[:-l])/(e@e))

# ── 4) ДИАГНОСТИКА: значимост (DM vs naive/ЕСО) + грешки ──
D2 = {}
for k in MODELS:
    e = (D[k]-D["y"]).values; env = (D["naive"]-D["y"]).values; eeso = (D["ЕСО"]-D["y"]).values
    se = np.sqrt(nw(e, NWLAG)/n); bp = 2*(1-st.norm.cdf(abs(e.mean()/se)))
    lb = acorr_ljungbox(e, lags=[H], return_df=True)["lb_pvalue"].iloc[0]
    arch = het_arch(e, nlags=48)[1]; jb = jarque_bera(e)[1]
    adf = adfuller(e, autolag="AIC")[1]; kp = kpss(e, nlags="auto")[1]
    D2[k] = {"DM vs naive": f"{dm_p(e, env):.1e}", "DM vs ЕСО": f"{dm_p(e, eeso):.1e}",
             "bias p(HAC)": f"{bp:.2f}", "Ljung-Box(24)": f"{lb:.1e}",
             "ACF1": f"{acf(e,1):.2f}", "ACF24": f"{acf(e,H):.2f}", "ARCH p": f"{arch:.1e}",
             "Norm(JB) p": f"{jb:.1e}", "ADF p": f"{adf:.2f}", "KPSS p": f"{kp:.2f}"}
save_table(pd.DataFrame(D2).T, FIG/"pipeline_diagnostics.png",
           "Диагностика 24ч: значимост (DM vs naive/ЕСО, p<0.05=значимо по-добър) + грешки "
           "(Ljung/ACF=автокор · ARCH=хетероскед · JB=норм · ADF/KPSS=стац)", (14, 3.5))
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
print("5) конформал/Winkler ✓")

# ── 6) ИЗБОР: НАЙ-НИСКО MAPE + НАЙ-НИСЪК OVERFIT + НАЙ-ДОБЪР WINKLER (равни тегла, норм.) ──
sc_df = pd.DataFrame({k: {"MAPE": rowsm[k]["MAPE"], "overfit": rowsm[k]["MAE"]/train_mae[k], "Winkler": wink[k]}
                      for k in MODELS}).T
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
print(f"6) избор ✓  → {SELECTED}")

# ── 7) LEARNING CURVE на избрания (train vs test MAE спрямо обема обучение) ──
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
for _, r in LC.iterrows():
    plt.annotate(f"{r['val']/r['train']:.2f}", (r["days"], (r["train"]+r["val"])/2), fontsize=7, ha="center", color="gray")
plt.xlabel("дни обучение (обем train)"); plt.ylabel("MAE (MW)")
plt.title(f"{SELECTED} (24ч) — Learning curve (train vs test) · сивото = overfit gap")
plt.legend(); plt.grid(alpha=.3)
plt.savefig(FIG/f"pipeline_learning_curve_{SELECTED}.png", dpi=150, bbox_inches="tight"); plt.close()
print("7) learning curve ✓")

# ── 8) ФИНАЛНА ГРАФИКА за избрания: scatter(adjR²) + хистограма + actual/pred + Mondrian 90% ──
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
# (b) хистограма на грешките
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
out = W[~W["in"]]; ax3.scatter(out.index, out["y"], s=20, color="#1d4ed8", zorder=5, label="извън лентата")
ax3.set_title(f"{SELECTED} (24ч): Actual vs Predicted + 90% Mondrian интервал (покритие {cov:.1f}%)")
ax3.set_ylabel("товар (MW)"); ax3.legend(loc="upper right", fontsize=8); ax3.grid(alpha=.3)
fig.suptitle(f"ИЗБРАН МОДЕЛ (24ч): {SELECTED}", fontsize=15, weight="bold")
plt.tight_layout(); plt.savefig(FIG/f"pipeline_final_{SELECTED}.png", dpi=150, bbox_inches="tight"); plt.close()
print(f"8) финална графика ✓ (Mondrian покритие {cov:.1f}%)")

print(f"\nГотово — PNG в {FIG}: metrics, significant, corr×4, diagnostics, intervals, selection, "
      f"learning_curve, final_{SELECTED}")

if DO_UPLOAD:
    # Качи цялата папка → s3://.../data/results/1d/<png>
    upload(FIG, prefix="data/results")