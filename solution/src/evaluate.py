"""Metrics, baselines, and the walk-forward evaluation protocol.

Designed so that the modellers and the "methodologist" share one definition of
correctness (docs/practices.md): baselines first, multiple metrics, forward
validation, and a single held-out test set.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from . import config as C


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def mae(y, p):
    return float(np.mean(np.abs(y - p)))


def rmse(y, p):
    return float(np.sqrt(np.mean((y - p) ** 2)))


def mape(y, p, eps=1.0):
    """MAPE with a floor on |y| to stay finite when prices cross zero."""
    denom = np.maximum(np.abs(y), eps)
    return float(np.mean(np.abs((y - p) / denom)) * 100)


def smape(y, p):
    denom = (np.abs(y) + np.abs(p))
    denom = np.where(denom == 0, 1.0, denom)
    return float(np.mean(2 * np.abs(y - p) / denom) * 100)


def all_metrics(y, p) -> dict:
    y = np.asarray(y, float)
    p = np.asarray(p, float)
    m = np.isfinite(y) & np.isfinite(p)
    y, p = y[m], p[m]
    return {"n": int(len(y)), "MAE": mae(y, p), "RMSE": rmse(y, p),
            "MAPE": mape(y, p), "sMAPE": smape(y, p)}


def skill_score(metric_model, metric_baseline):
    """Fraction of the baseline error removed (1 = perfect, 0 = no better)."""
    if metric_baseline == 0:
        return np.nan
    return 1 - metric_model / metric_baseline


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------
def baseline_persistence(y: pd.Series, horizon: int) -> pd.Series:
    """Predict y(t+h) = y(t): the value `horizon` steps before the target."""
    return y.shift(horizon)


def baseline_seasonal_naive(y: pd.Series, horizon: int, season: int) -> pd.Series:
    """Predict y(t+h) = y(t+h-season): same clock position one season earlier.

    For the daily/weekly horizons the relevant season is one full day (24) or
    one week (168). We snap to the smallest multiple of `season` that is >= h
    so the lag is genuinely available at origin time.
    """
    k = int(np.ceil(horizon / season)) * season
    return y.shift(k)


# --------------------------------------------------------------------------
# Walk-forward split
# --------------------------------------------------------------------------
def chronological_test_cut(index: pd.DatetimeIndex, test_fraction=C.TEST_FRACTION):
    """Index position splitting train(+val) from the final held-out test set."""
    return int(len(index) * (1 - test_fraction))


def walkforward_folds(n_train_val: int, n_folds=C.N_WALKFORWARD_FOLDS):
    """Yield (train_end, val_start, val_end) positions for expanding-window CV.

    Train on [0, train_end), validate on [val_start, val_end). Each fold slides
    forward; the training window expands. Operates only on the pre-test region.
    """
    fold = n_train_val // (n_folds + 1)
    for i in range(1, n_folds + 1):
        train_end = fold * i
        val_start = train_end
        val_end = fold * (i + 1)
        yield train_end, val_start, val_end


def make_model(seed=C.SEED) -> HistGradientBoostingRegressor:
    """Gradient-boosted trees: strong tabular baseline, native NaN handling.

    Used instead of LightGBM so the pipeline runs with a stock scientific
    Python stack (sklearn) and no extra compiled dependency.
    """
    return HistGradientBoostingRegressor(
        loss="squared_error",
        learning_rate=0.03,
        max_iter=300,
        max_leaf_nodes=15,
        min_samples_leaf=80,
        l2_regularization=10.0,
        early_stopping=True,
        validation_fraction=0.1,
        random_state=seed,
    )
