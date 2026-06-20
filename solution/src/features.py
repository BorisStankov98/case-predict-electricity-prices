"""Feature engineering with a strict no-look-ahead contract.

The golden rule (docs/practices.md): to predict ``y(t+h)`` every feature must be
knowable at the *forecast origin* ``t``. Two kinds of features satisfy this:

1. **Calendar features of the target time ``t+h``.** The clock and the holiday
   calendar are deterministic and known arbitrarily far ahead, so using them at
   the target time is legitimate (and is how real day-ahead models work).
2. **Lagged values of series observed up to ``t``.** We build lags/rolling stats
   and then, in :mod:`models`, align them so only origin-time information feeds a
   given horizon.

Weather at the target time is used as a *proxy for a weather forecast*. In
production you would plug in an NWP forecast; the provided data only contains
(re)analysis, so we use target-time weather and flag the assumption loudly in
the README. Everything else respects the origin-time rule.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C


# --------------------------------------------------------------------------
# Bulgarian public holidays (the `holidays` package isn't installed in the
# grading env, so we hard-code the fixed + computed dates for the data span,
# 2025-12 .. 2026-06). Strong load/price signal per docs/data.md.
# --------------------------------------------------------------------------
_BG_HOLIDAYS = {
    # 2025
    "2025-12-24", "2025-12-25", "2025-12-26",
    # 2026
    "2026-01-01",                       # New Year
    "2026-03-03",                       # Liberation Day
    "2026-04-10", "2026-04-11",         # Orthodox Good Friday / Holy Saturday
    "2026-04-12", "2026-04-13",         # Orthodox Easter Sun/Mon
    "2026-05-01",                       # Labour Day
    "2026-05-06",                       # St George's Day
    "2026-05-24",                       # Education & Culture Day
}
_BG_HOLIDAYS = pd.to_datetime(sorted(_BG_HOLIDAYS)).date


def calendar_features(index: pd.DatetimeIndex, tz_local: str = "Europe/Sofia") -> pd.DataFrame:
    """Deterministic calendar features computed in *local* Bulgarian time.

    Human activity (and therefore load/price) follows the local clock, so we
    convert the UTC index to Europe/Sofia before extracting hour/day.
    """
    local = index.tz_convert(tz_local)
    df = pd.DataFrame(index=index)
    df["hour"] = local.hour
    df["dow"] = local.dayofweek
    df["month"] = local.month
    df["doy"] = local.dayofyear
    df["is_weekend"] = (local.dayofweek >= 5).astype(int)
    df["is_holiday"] = np.isin(local.date, _BG_HOLIDAYS).astype(int)
    df["is_off_day"] = ((df["is_weekend"] == 1) | (df["is_holiday"] == 1)).astype(int)
    # Cyclical encodings so the model sees 23:00 and 00:00 as adjacent.
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["doy_sin"] = np.sin(2 * np.pi * df["doy"] / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * df["doy"] / 365.25)
    return df


def lag_features(s: pd.Series, lags, rolling=None, prefix=None) -> pd.DataFrame:
    """Lagged and rolling-window features of a single series.

    All rolling windows are causal (they end at the current row), so when these
    are later shifted to a forecast origin they never peek past it.
    """
    prefix = prefix or s.name
    out = pd.DataFrame(index=s.index)
    for L in lags:
        out[f"{prefix}_lag{L}"] = s.shift(L)
    if rolling:
        for w in rolling:
            out[f"{prefix}_rollmean{w}"] = s.shift(1).rolling(w).mean()
            out[f"{prefix}_rollstd{w}"] = s.shift(1).rolling(w).std()
    return out


def build_feature_frame(df: pd.DataFrame, target: str, horizon: int,
                        weather_cols=None, extra_cols=None,
                        lag_target=True) -> tuple[pd.DataFrame, pd.Series]:
    """Assemble (X, y) for a *direct* h-step-ahead forecast of ``target``.

    Strategy
    --------
    We predict ``y(t+h)`` from features available at ``t``. Concretely we build
    the feature matrix indexed by the *target* time ``t+h`` so that:

    * calendar features are evaluated at ``t+h`` (legitimate, deterministic);
    * weather columns are taken at ``t+h`` (forecast proxy, documented);
    * lagged drivers are taken at the origin ``t`` -- i.e. shifted by an extra
      ``h`` relative to the target row -- guaranteeing no look-ahead.

    Returns aligned ``(X, y)`` with rows containing a NaN *target* dropped.
    """
    y = df[target]

    # 1) Calendar at the target timestamp.
    feats = [calendar_features(df.index)]

    # 2) Origin-time lags of the target itself. The forecast origin is t = T - h
    #    (T = target row). The freshest target value knowable at the origin is
    #    y(t) = y(T-h) = y.shift(h). We build everything from this origin-time
    #    view `yo`, so nothing can peek past the origin.
    if lag_target:
        yo = y.shift(horizon)                       # origin-time view of target
        tgt = pd.DataFrame(index=df.index)
        # Fresh recent lags + same-hour-yesterday (+24) + same-hour-last-week (+168).
        for extra in (0, 1, 2, C.DAY, C.WEEK):
            tgt[f"{target}_lag{horizon + extra}"] = yo.shift(extra)
        for w in (C.DAY, C.WEEK):                    # causal rolling stats
            tgt[f"{target}_rollmean{w}"] = yo.rolling(w).mean()
            tgt[f"{target}_rollstd{w}"] = yo.rolling(w).std()
        feats.append(tgt)

    # 3) Origin-time values of exogenous drivers (net position, flows, outages,
    #    ENTSO-E forecasts...). Shift by h so only origin info is used.
    if extra_cols:
        ex = df[extra_cols].shift(horizon)
        ex.columns = [f"{c}_origin" for c in ex.columns]
        feats.append(ex)

    # 4) Weather at target time (forecast proxy). NOT shifted.
    if weather_cols:
        feats.append(df[weather_cols])

    X = pd.concat(feats, axis=1)
    # Keep rows with a valid target; models tolerate NaN features natively.
    mask = y.notna()
    return X[mask], y[mask]
