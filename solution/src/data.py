"""Data loading and the joined, analysis-ready datasets.

Responsibilities
----------------
* Read each raw CSV from ``data/`` exactly as it lands from the scrapers.
* Convert every timestamp to one canonical timezone (UTC).
* Resolve the resolution-mixing problem (docs/data.md #3) by resampling
  everything onto a single hourly grid for the main models, while keeping
  the native 15-minute price for the 15-minute-ahead horizon.

The two public entry points are :func:`build_hourly_dataset` (load, generation,
weather, price + cross-border, all hourly) and :func:`load_price_qh` (the
native 15-minute day-ahead price series).
"""
from __future__ import annotations

import pandas as pd

from . import config as C


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------
def _read_ts_csv(path, value_cols=None) -> pd.DataFrame:
    """Read a one-timestamp-column CSV into a tz-aware, UTC-indexed frame."""
    df = pd.read_csv(path)
    ts_col = df.columns[0]
    # ENTSO-E / weather timestamps may or may not carry an offset; let pandas
    # infer, then normalise to UTC.
    idx = pd.to_datetime(df[ts_col], utc=True, errors="coerce")
    df = df.drop(columns=[ts_col])
    df.index = idx
    df.index.name = "timestamp"
    df = df[~df.index.isna()]
    df = df[~df.index.duplicated(keep="first")]  # guard against DST doubling
    df = df.sort_index()
    if value_cols is not None:
        df = df.rename(columns=value_cols)
    return df


def _to_hourly(df: pd.DataFrame, how: str = "mean") -> pd.DataFrame:
    """Resample to the canonical hourly grid.

    ``how='mean'`` for power [MW] / prices, ``how='sum'`` for energy. All our
    series are MW (instantaneous power averaged over the MTU) or prices, so
    mean is the correct default (docs/data.md #3).
    """
    res = df.resample(C.HOURLY_FREQ)
    return res.mean() if how == "mean" else res.sum()


# --------------------------------------------------------------------------
# Individual sources
# --------------------------------------------------------------------------
def load_price_qh() -> pd.Series:
    """Native 15-minute ENTSO-E day-ahead price (EUR/MWh), UTC indexed.

    The case's headline target is the IBEX continuous-intraday price, but that
    series is capped at ~3 months and is patchy. The ENTSO-E day-ahead price is
    the recommended public substitute (docs/data.md): long, clean, 15-min since
    Oct 2025, and strongly related to intraday. We use it as the price target.
    """
    df = _read_ts_csv(C.ENTSOE_DIR / "prices_day_ahead.csv")
    s = df["prices_day_ahead"].astype(float)
    s.name = "price_eur_per_mwh"
    return s


def load_price_hourly() -> pd.Series:
    return _to_hourly(load_price_qh().to_frame(), how="mean")["price_eur_per_mwh"]


def load_load() -> pd.DataFrame:
    actual = _read_ts_csv(
        C.ENTSOE_DIR / "load_actual.csv", {"Actual Load": "load_mw"}
    )
    fc = _read_ts_csv(
        C.ENTSOE_DIR / "load_forecast_day_ahead.csv",
        {"Forecasted Load": "load_forecast_mw"},
    )
    return _to_hourly(actual).join(_to_hourly(fc), how="outer")


def load_generation() -> pd.DataFrame:
    """Generation per production type (+ total) and ENTSO-E day-ahead forecasts."""
    gen = _read_ts_csv(C.ENTSOE_DIR / "generation_per_type.csv")
    gen = _to_hourly(gen)
    gen.columns = [f"gen_{c.lower().replace(' ', '_').replace('/', '_')}_mw"
                   for c in gen.columns]
    gen["gen_total_mw"] = gen.sum(axis=1)

    gen_fc = _read_ts_csv(
        C.ENTSOE_DIR / "generation_forecast_day_ahead.csv",
        {"generation_forecast_day_ahead": "gen_forecast_mw"},
    )
    ws_fc = _read_ts_csv(C.ENTSOE_DIR / "wind_solar_forecast.csv")
    ws_fc = ws_fc.rename(columns={"Solar": "solar_forecast_mw",
                                  "Wind Onshore": "wind_forecast_mw"})
    out = gen.join(_to_hourly(gen_fc), how="outer")
    out = out.join(_to_hourly(ws_fc), how="outer")
    return out


def load_weather() -> pd.DataFrame:
    df = _read_ts_csv(C.WEATHER_DIR / "weather_bg_total.csv")
    if "source" in df.columns:
        df = df.drop(columns=["source"])
    df = df.apply(pd.to_numeric, errors="coerce")
    df.columns = [f"wx_{c}" for c in df.columns]
    return _to_hourly(df)


def load_net_position() -> pd.Series:
    df = _read_ts_csv(C.ENTSOE_DIR / "net_position.csv")
    s = _to_hourly(df)["net_position"]
    s.name = "net_position_mw"
    return s


def load_cross_border() -> pd.DataFrame:
    """Net physical flow per neighbour (import positive), hourly.

    net_flow = flow(X->BG) - flow(BG->X), so a positive number means BG is
    importing from neighbour X for that hour.
    """
    neighbours = ["RO", "GR", "RS", "MK", "TR"]
    cols = {}
    for n in neighbours:
        try:
            imp = _read_ts_csv(C.ENTSOE_DIR / f"physical_flows_{n}_to_BG.csv")
            exp = _read_ts_csv(C.ENTSOE_DIR / f"physical_flows_BG_to_{n}.csv")
            imp = _to_hourly(imp).iloc[:, 0]
            exp = _to_hourly(exp).iloc[:, 0]
            cols[f"net_flow_{n}_mw"] = imp.sub(exp, fill_value=0.0)
        except FileNotFoundError:
            continue
    return pd.DataFrame(cols)


def load_outages_hourly(index: pd.DatetimeIndex) -> pd.Series:
    """Total generation capacity unavailable (MW) at each hour.

    The outage file is event-style (one row per REMIT notification with a
    start/end and an *available* quantity). We turn it into a time series of
    "unavailable MW" = nominal_power - avail_qty, summed over events active at
    each hour. Reduced available capacity tightens supply and lifts price.
    """
    path = C.ENTSOE_DIR / "unavailability_generation_units.csv"
    df = pd.read_csv(path)
    start = pd.to_datetime(df["start"], utc=True, errors="coerce")
    end = pd.to_datetime(df["end"], utc=True, errors="coerce")
    unavail = (df["nominal_power"].astype(float)
               - df["avail_qty"].astype(float)).clip(lower=0)
    out = pd.Series(0.0, index=index, name="outage_unavail_mw")
    for s, e, mw in zip(start, end, unavail):
        if pd.isna(s) or pd.isna(e) or pd.isna(mw):
            continue
        out.loc[(out.index >= s) & (out.index < e)] += mw
    return out


# --------------------------------------------------------------------------
# Joined dataset
# --------------------------------------------------------------------------
def build_hourly_dataset() -> pd.DataFrame:
    """One coherent hourly frame: targets + raw drivers, UTC, documented units.

    Columns are suffixed with their unit (``_mw``, ``_eur_per_mwh``, ``wx_*``)
    per docs/practices.md. Missing cells are left as NaN on purpose: the
    gradient-boosting models consume NaN natively, and forward-filling here
    would risk hiding real gaps.
    """
    price = load_price_hourly()
    load = load_load()
    gen = load_generation()
    weather = load_weather()
    netpos = load_net_position()
    flows = load_cross_border()

    df = pd.concat([price, load, gen, weather, netpos, flows], axis=1)
    df = df.sort_index()
    # Trim to the span where we at least have the price target.
    df = df.loc[price.index.min(): price.index.max()]
    df["outage_unavail_mw"] = load_outages_hourly(df.index)
    df.index.name = "timestamp"
    return df


if __name__ == "__main__":  # quick manual smoke test
    d = build_hourly_dataset()
    print(d.shape)
    print(d.columns.tolist())
    print(d.describe().T[["count", "mean", "min", "max"]])
