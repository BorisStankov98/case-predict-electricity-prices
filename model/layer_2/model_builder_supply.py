"""
model_builder_supply.py — Layer 2 (SUPPLY) model training + evaluation.

Reads the supply feature table from S3, does a single chronological train/test
split, standardises, trains six regressors plus a naive benchmark, scores them
on the held-out test period, and writes the figures to data/results/supply/
(uploaded by default). Counterpart to the Layer 1 model builders.

Input : data/processed/features_supply_long.csv (supply target + weather/calendar/outage features)
Output (data/results/supply/, PNG):
  supply_series.png        full supply series (MW, local BG time)
  supply_predictions.png   test actual vs each model (standardised units)
  supply_metrics.png       MSE / RMSE / MAE / MAPE table per model

Models (kept from the original supply-side analysis): Lasso(α=0.01),
Ridge(α=1), ElasticNet(α=0.01), DecisionTree, RandomForest, GradientBoosting,
plus naive (last training value). Features (weather + calendar + outage dummies)
and the target are standardised per split, as in the original.

Split: train < 2025-10-01 (local), test from 2025-10-01 to the end.

Usage:
    python model_builder_supply.py            # train + plot + upload to data/results/supply/ (S3 default)
    python model_builder_supply.py --local    # train + plot locally only (no upload)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sklearn import linear_model
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.metrics import (mean_squared_error, root_mean_squared_error,
                             mean_absolute_error, mean_absolute_percentage_error)

# Make the shared tools/ dir importable (for upload_s3) from model/layer_2/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from upload_s3 import read_csv, upload  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DO_UPLOAD = True  # always persist; backend (s3/local) chosen in upload_s3
LOCAL = "Europe/Sofia"
FEATURES_KEY = "data/processed/features_supply_long.csv"
SPLIT = "2025-10-01"                              # train before, test from
# PNGs go here locally, then uploaded to S3 under data/results/supply/.
FIG = Path(__file__).resolve().parents[1] / "results" / "supply"
FIG.mkdir(parents=True, exist_ok=True)

TARGET = "supply"


def standardize(df: pd.DataFrame) -> pd.DataFrame:
    """Per-column standardisation; binary dummy columns are left as-is."""
    out = df.copy()
    for c in df.columns:
        s = df[c]
        if set(pd.unique(s.dropna())) <= {0, 1}:     # keep dummies 0/1
            continue
        sd = s.std()
        out[c] = (s - s.mean()) / sd if sd else 0.0
    return out


def main() -> int:
    print(f"features: {FEATURES_KEY}")
    F = read_csv(FEATURES_KEY, index_col=0)
    F.index = pd.to_datetime(F.index, utc=True).tz_convert(LOCAL)
    F = F.sort_index()

    feat_cols = [c for c in F.columns if c != TARGET]

    # ── chronological split ──
    split_ts = pd.Timestamp(SPLIT, tz=LOCAL)
    train, test = F[F.index < split_ts], F[F.index >= split_ts]
    print(f"  train: {len(train)} rows ({train.index.min()} → {train.index.max()})")
    print(f"  test:  {len(test)} rows ({test.index.min()} → {test.index.max()})")

    # ── standardise features + target (per split, as in the original) ──
    Xtr = standardize(train[feat_cols]); ytr = (train[TARGET] - train[TARGET].mean()) / train[TARGET].std()
    Xte = standardize(test[feat_cols]);  yte = (test[TARGET] - test[TARGET].mean()) / test[TARGET].std()

    # ── models ──
    models = {
        "lasso": linear_model.Lasso(alpha=0.01),
        "ridge": linear_model.Ridge(alpha=1),
        "elastic_net": linear_model.ElasticNet(alpha=0.01, random_state=0),
        "decision_tree": DecisionTreeRegressor(random_state=0, max_depth=10,
                                               min_samples_split=30, min_samples_leaf=30),
        "random_forest": RandomForestRegressor(max_depth=10, min_samples_split=30,
                                               min_samples_leaf=30, random_state=0),
        "gradient_boosting": GradientBoostingRegressor(max_depth=10, min_samples_split=30,
                                                       min_samples_leaf=30, random_state=0),
    }
    preds = pd.DataFrame({"supply": yte.values}, index=test.index)
    preds["naive"] = ytr.iloc[-1]                    # last training value
    for name, mdl in models.items():
        mdl.fit(Xtr, ytr)
        preds[name] = mdl.predict(Xte)
        print(f"  {name:<18} train R²={mdl.score(Xtr, ytr):+.3f}  test R²={mdl.score(Xte, yte):+.3f}")

    # ── metrics on the test period ──
    pred_cols = [c for c in preds.columns if c != "supply"]
    metrics = pd.DataFrame(index=["mse", "rmse", "mae", "mape"], columns=pred_cols, dtype=float)
    for c in pred_cols:
        metrics.loc["mse", c] = mean_squared_error(preds["supply"], preds[c])
        metrics.loc["rmse", c] = root_mean_squared_error(preds["supply"], preds[c])
        metrics.loc["mae", c] = mean_absolute_error(preds["supply"], preds[c])
        metrics.loc["mape", c] = mean_absolute_percentage_error(preds["supply"], preds[c])

    # ── figures ──
    # 1) full supply series (MW)
    plt.figure(figsize=(16, 6))
    F[TARGET].plot(color="#2563eb", lw=0.6)
    plt.title("Bulgaria supply (generation + net imports), MW — local BG time")
    plt.ylabel("MW"); plt.xlabel("")
    plt.tight_layout(); plt.savefig(FIG / "supply_series.png", dpi=140); plt.close()

    # 2) test actual vs predictions (standardised units)
    plt.figure(figsize=(16, 7))
    plt.plot(preds.index, preds["supply"], color="black", lw=1.4, label="actual")
    for c in pred_cols:
        plt.plot(preds.index, preds[c], lw=0.8, alpha=0.8, label=c)
    plt.title("Supply — test period: actual vs models (standardised)")
    plt.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=4, fontsize=9)
    plt.tight_layout(); plt.savefig(FIG / "supply_predictions.png", dpi=140); plt.close()

    # 3) metrics table
    fig, ax = plt.subplots(figsize=(12, 2.6)); ax.axis("off")
    tbl = ax.table(cellText=metrics.round(4).values, colLabels=metrics.columns,
                   rowLabels=metrics.index, loc="center", cellLoc="center")
    tbl.auto_set_font_size(False); tbl.set_fontsize(9); tbl.scale(1, 1.5)
    ax.set_title("Supply — test metrics (standardised units)", pad=12)
    plt.tight_layout(); plt.savefig(FIG / "supply_metrics.png", dpi=140, bbox_inches="tight"); plt.close()

    print(f"\n✅ figures → {FIG}/  ({len(list(FIG.glob('*.png')))} PNG)")
    print(metrics.round(4).to_string())

    if DO_UPLOAD:
        upload(FIG, prefix="data/results")           # → data/results/supply/<png>
    return 0


if __name__ == "__main__":
    sys.exit(main())
