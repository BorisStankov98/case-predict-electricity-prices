"""The three forecasting layers.

Layer 1  consumption (load)        y = load_mw
Layer 2  supply (generation)       y = gen_total_mw
Layer 3  price                     y = price_eur_per_mwh, fed Layer-1 and
                                      Layer-2 forecasts (the layered design).

Each layer is a thin wrapper around the same recipe: build a no-look-ahead
feature frame (features.build_feature_frame), fit a gradient-boosted model per
horizon, and predict. Layer 3 additionally consumes the *out-of-sample*
forecasts produced by Layers 1 and 2 so the chain mirrors how a real market
forms a price from forecasted demand and supply.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import evaluate as E
from . import features as F


# Weather columns the demand/supply layers are allowed to "forecast".
WEATHER_DEMAND = ["wx_temperature_2m", "wx_relative_humidity_2m",
                  "wx_cloud_cover", "wx_shortwave_radiation"]
WEATHER_SUPPLY = ["wx_wind_speed_10m", "wx_wind_speed_100m",
                  "wx_shortwave_radiation", "wx_direct_normal_irradiance",
                  "wx_cloud_cover", "wx_temperature_2m"]

# Exogenous origin-time drivers per layer.
EXO_DEMAND = ["net_position_mw"]
EXO_SUPPLY = ["outage_unavail_mw", "net_position_mw",
              "wind_forecast_mw", "solar_forecast_mw"]
EXO_PRICE = ["net_position_mw", "outage_unavail_mw"]


ALPHA_GRID = (0.0, 0.3, 0.5, 0.7, 1.0)


def _fit_predict_residual(Xtr, rtr, Xte, anchor_te, seed=C.SEED, alpha=1.0):
    """Fit the model on the residual-over-anchor, return anchored predictions.

    The model learns ``r = y - anchor`` (a near-stationary correction) and the
    prediction is ``anchor + alpha * model(X)``. The anchoring makes the model
    degrade gracefully to persistence on non-stationary series (load/generation
    have a strong seasonal level shift across the ~6-month window, which
    absolute-level trees cannot extrapolate). ``alpha`` shrinks the learned
    correction toward the anchor to control variance; ``alpha=0`` *is*
    persistence, ``alpha=1`` is the full ML correction.
    """
    model = E.make_model(seed)
    model.fit(Xtr, rtr)
    pred = anchor_te.values + alpha * model.predict(Xte)
    return model, pd.Series(pred, index=Xte.index)


def _select_alpha(X, y, anchor, train_end, seed=C.SEED):
    """Pick the shrinkage alpha on a validation split carved from training data.

    Uses the last ~25% of the *training* region as an internal validation set.
    The held-out test set is never touched here -- this keeps hyperparameter
    selection honest (docs/practices.md: "one held-out test set").
    """
    val_start = int(train_end * 0.75)
    r = y - anchor
    fit_mask = r.iloc[:val_start].notna() & anchor.iloc[:val_start].notna()
    if fit_mask.sum() < 50:
        return 0.5  # too little data to choose; use a safe middle shrinkage
    model = E.make_model(seed)
    model.fit(X.iloc[:val_start][fit_mask.values], r.iloc[:val_start][fit_mask.values])
    corr = model.predict(X.iloc[val_start:train_end])
    a_val = anchor.iloc[val_start:train_end].values
    y_val = y.iloc[val_start:train_end].values
    best_alpha, best_mae = 0.5, np.inf
    for a in ALPHA_GRID:
        mae = np.nanmean(np.abs(y_val - (a_val + a * corr)))
        if mae < best_mae:
            best_mae, best_alpha = mae, a
    return best_alpha


def _fit_predict_oos(X: pd.DataFrame, y: pd.Series, anchor: pd.Series,
                     seed=C.SEED) -> pd.Series:
    """Out-of-sample residual-anchored predictions via expanding walk-forward.

    For every walk-forward fold we train on the past and predict the future
    block, so the returned series is genuinely out-of-sample everywhere it is
    defined. These are the forecasts Layer 3 is allowed to consume.
    """
    n = len(X)
    r = y - anchor
    preds = pd.Series(np.nan, index=X.index)
    fold = n // 6
    for i in range(1, 6):
        tr_end = fold * i
        bl_end = fold * (i + 1) if i < 5 else n
        tr = slice(0, tr_end)
        bl = slice(tr_end, bl_end)
        mask = r.iloc[tr].notna() & anchor.iloc[tr].notna()
        alpha = _select_alpha(X, y, anchor, tr_end, seed)
        _, p = _fit_predict_residual(
            X.iloc[tr][mask.values], r.iloc[tr][mask.values],
            X.iloc[bl], anchor.iloc[bl], seed, alpha=alpha)
        preds.iloc[tr_end:bl_end] = p.values
    return preds


class Layer:
    """A single (target, horizon) forecasting unit."""

    def __init__(self, df, target, horizon, weather_cols, exo_cols,
                 lag_target=True, name=""):
        self.df = df
        self.target = target
        self.horizon = horizon
        self.name = name
        self.X, self.y = F.build_feature_frame(
            df, target=target, horizon=horizon,
            weather_cols=weather_cols, extra_cols=exo_cols,
            lag_target=lag_target,
        )
        # Persistence anchor available at the forecast origin: y(t) = y(T-h).
        # The model regresses the residual y - anchor (see _fit_predict_residual).
        self.anchor = df[target].shift(horizon).reindex(self.y.index)

    def add_features(self, extra: pd.DataFrame):
        """Join additional aligned feature columns (used to inject L1/L2 forecasts)."""
        self.X = self.X.join(extra, how="left")

    def evaluate(self, season: int) -> dict:
        """Fit on train, score on the held-out test set vs. baselines."""
        cut = E.chronological_test_cut(self.X.index)
        Xte = self.X.iloc[cut:]
        yte = self.y.iloc[cut:]

        # Residual-over-anchor fit on the training region; shrinkage chosen on
        # an internal validation split (never on the test set).
        alpha = _select_alpha(self.X, self.y, self.anchor, cut)
        self.alpha = alpha
        r = self.y - self.anchor
        mtr = r.iloc[:cut].notna() & self.anchor.iloc[:cut].notna()
        model, pred = _fit_predict_residual(
            self.X.iloc[:cut][mtr.values], r.iloc[:cut][mtr.values],
            Xte, self.anchor.iloc[cut:], alpha=alpha)

        # Baselines on the same test rows.
        pers = E.baseline_persistence(self.y, self.horizon).reindex(yte.index)
        seas = E.baseline_seasonal_naive(self.y, self.horizon, season).reindex(yte.index)

        res = {
            "model": E.all_metrics(yte, pred),
            "persistence": E.all_metrics(yte, pers),
            "seasonal_naive": E.all_metrics(yte, seas),
        }
        res["skill_vs_persistence_MAE"] = E.skill_score(
            res["model"]["MAE"], res["persistence"]["MAE"])
        res["skill_vs_seasonal_MAE"] = E.skill_score(
            res["model"]["MAE"], res["seasonal_naive"]["MAE"])
        self._fitted = model
        self._test = (yte, pred, pers, seas)
        return res

    def oos_forecast(self) -> pd.Series:
        """Whole-span out-of-sample forecast (for feeding the next layer)."""
        return _fit_predict_oos(self.X, self.y, self.anchor)


def build_layers(df: pd.DataFrame):
    """Construct all (layer, horizon) units for the hourly horizons.

    Returns a dict keyed by (layer_name, horizon_label).
    """
    layers = {}
    for hlabel, h in C.HORIZONS_HOURLY.items():
        # Weekly seasonal-naive (same hour, same weekday, last week). It is a
        # strong load/price baseline and -- unlike a daily anchor -- stays valid
        # at both the 24h and 1-week horizons (168 >= h in both cases).
        season = C.WEEK

        # Layer 1 -- consumption.
        l1 = Layer(df, "load_mw", h, WEATHER_DEMAND, EXO_DEMAND,
                   name=f"L1_load_{hlabel}")

        # Layer 2 -- supply (total generation).
        l2 = Layer(df, "gen_total_mw", h, WEATHER_SUPPLY, EXO_SUPPLY,
                   name=f"L2_gen_{hlabel}")

        # Layer 3 -- price. Starts from price's own features, then is fed the
        # out-of-sample forecasts of L1 and L2 at the matching horizon.
        l3 = Layer(df, "price_eur_per_mwh", h, None, EXO_PRICE,
                   name=f"L3_price_{hlabel}")
        l1_oos = l1.oos_forecast().rename("load_forecast_L1")
        l2_oos = l2.oos_forecast().rename("gen_forecast_L2")
        l3.add_features(pd.concat([l1_oos, l2_oos], axis=1))

        layers[("L1_load", hlabel)] = (l1, season)
        layers[("L2_gen", hlabel)] = (l2, season)
        layers[("L3_price", hlabel)] = (l3, season)
    return layers


# --------------------------------------------------------------------------
# 15-minute price horizon (native QH series, persistence-dominated)
# --------------------------------------------------------------------------
def evaluate_qh_price(price_qh: pd.Series) -> dict:
    """15-minutes-ahead price on the native 15-min series.

    At this horizon persistence is famously hard to beat (README "realistic
    expectations"). We report a gradient-boosted model that uses only QH price
    lags + calendar, against persistence and a daily seasonal-naive (96 QH).
    """
    h = C.HORIZON_QH_STEPS
    df = price_qh.to_frame()
    X, y = F.build_feature_frame(df, "price_eur_per_mwh", h,
                                 weather_cols=None, extra_cols=None,
                                 lag_target=True)
    cut = E.chronological_test_cut(X.index)
    anchor = price_qh.shift(h).reindex(y.index)
    alpha = _select_alpha(X, y, anchor, cut)
    r = y - anchor
    mtr = r.iloc[:cut].notna() & anchor.iloc[:cut].notna()
    model, pred = _fit_predict_residual(
        X.iloc[:cut][mtr.values], r.iloc[:cut][mtr.values],
        X.iloc[cut:], anchor.iloc[cut:], alpha=alpha)
    yte = y.iloc[cut:]
    pers = E.baseline_persistence(y, h).reindex(yte.index)
    seas = E.baseline_seasonal_naive(y, h, season=96).reindex(yte.index)  # 1 day = 96 QH
    res = {
        "model": E.all_metrics(yte, pred),
        "persistence": E.all_metrics(yte, pers),
        "seasonal_naive": E.all_metrics(yte, seas),
    }
    res["skill_vs_persistence_MAE"] = E.skill_score(
        res["model"]["MAE"], res["persistence"]["MAE"])
    return res
