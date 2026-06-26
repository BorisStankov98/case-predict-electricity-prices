"""
transform_1_week_forecast_local_time.py — reproducible 1-WEEK master builder
(raw → canonical hourly master, 168h horizon).

Reads the raw inputs straight from S3 (data/raw/), assembles everything
DIRECTLY in LOCAL BG time (Europe/Sofia, tz-aware, DST), and uploads the result
to S3 data/processed/. S3 is the source of truth — no local data file is.

Inputs (S3, data/raw/, located by stable name fragment):
  load_actual              (Actual Load — tz-aware ENTSO-E)
  load_forecast_day_ahead  (Forecasted Load — ESO; tz-aware ENTSO-E)
  weather_bg_total         (ACTUAL weather, naive — fixed UTC+3, NO DST)
  days_off                 (BG calendar)
      → hourly master (load + ESO forecast + ACTUAL weather + is_day_off)
      → FULL history (from the start of the load series, ~2022-09-30)
      → grid + index in LOCAL BG time → timestamp_local
      → data/processed/master_1week_long.csv

Source time zones (each converted to local by its own true zone):
  • load_actual / load_forecast : tz-aware (offset in the file) → tz_convert("Europe/Sofia").
  • weather_bg_total : naive, but empirically established to be a **fixed UTC+3
      without DST** (ghi centroid constant across seasons; temp cross-correlation
      against the UTC forecast file peaks at lag −3h). So it is localized as
      UTC+3, then converted to Europe/Sofia.

Why ACTUAL weather (not a forecast, as in the 24h master)?
  At the 168h gate a day-ahead weather forecast for hour T is NOT available
  (the forecast only runs 1 day ahead). The honest proxy is last week's ACTUAL
  weather (lag168) — known at the gate. Bonus: actual weather reaches back to
  2022-01 (vs 2024-02-17 for the forecast) → a longer train.

Usage:
    python transform_1_week_forecast_local_time.py            # build only (writes local CSV)
    python transform_1_week_forecast_local_time.py --upload   # build + upload to data/processed
"""
import sys
from datetime import timezone, timedelta
from pathlib import Path

import pandas as pd

# Make the shared tools/ dir importable (for upload_s3) from this subfolder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from upload_s3 import read_csv, find_key, upload_processed  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---- S3 inputs (located by stable name fragment under data/raw/) ----
NAME_LOAD_ACTUAL = "load_actual"
NAME_LOAD_FORECAST = "load_forecast_day_ahead"
NAME_WEATHER = "weather_bg_total"
NAME_DAYS_OFF = "days_off"

# ---- output ----
OUT_NAME = "master_1week_long.csv"

LOCAL = "Europe/Sofia"
WX_TZ = timezone(timedelta(hours=3))           # weather_bg_total = naive UTC+3 (established)
WMAP = {"temp_c": "temperature_2m", "wind10_ms": "wind_speed_10m", "wind100_ms": "wind_speed_100m",
        "wind_dir_100m_deg": "wind_direction_100m", "ghi_wm2": "shortwave_radiation",
        "dni_wm2": "direct_normal_irradiance", "cloud_pct": "cloud_cover",
        "precip_mm": "precipitation", "rh_pct": "relative_humidity_2m"}
COLS = ["load_actual_mw", "load_forecast_mw"] + list(WMAP.keys()) + ["is_day_off"]


def read_series_local(df, col, name):
    """tz-aware ENTSO-E series → directly in LOCAL BG time."""
    ts = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(LOCAL)
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").values, index=ts, name=name)
    return s[~s.index.duplicated(keep="first")].sort_index()


def resolve(name: str) -> str:
    key = find_key(name)
    if key is None:
        raise SystemExit(f"No S3 object in data/raw/ matching name: {name!r}")
    return key


def main() -> int:
    do_upload = "--upload" in sys.argv

    # 1) load + ESO forecast (tz-aware → local)
    la_key, lf_key = resolve(NAME_LOAD_ACTUAL), resolve(NAME_LOAD_FORECAST)
    print(f"load actual:   {la_key}")
    print(f"load forecast: {lf_key}")
    load_a = read_series_local(read_csv(la_key), "Actual Load", "load_actual_mw")
    load_f = read_series_local(read_csv(lf_key), "Forecasted Load", "load_forecast_mw")

    # 2) ACTUAL weather (naive UTC+3 → local)
    wx_key = resolve(NAME_WEATHER)
    print(f"weather: {wx_key}")
    WX = read_csv(wx_key)
    WX.index = pd.DatetimeIndex(pd.to_datetime(WX["timestamp"])).tz_localize(WX_TZ).tz_convert(LOCAL)
    WX = WX[~WX.index.duplicated(keep="first")].sort_index()

    # 3) LOCAL hourly grid (Europe/Sofia, DST-aware): load start → end
    start = max(load_a.index.min(), WX.index.min())
    endts = min(load_a.index.max(), WX.index.max())
    grid = pd.date_range(start, endts, freq="1h", tz=LOCAL)
    M = pd.DataFrame(index=grid)
    M["load_actual_mw"] = load_a.reindex(grid)
    M["load_forecast_mw"] = load_f.reindex(grid)
    for dst, src in WMAP.items():
        M[dst] = pd.to_numeric(WX[src], errors="coerce").reindex(grid)

    # 4) is_day_off by LOCAL date (index is already local)
    doff_key = resolve(NAME_DAYS_OFF)
    print(f"days off: {doff_key}")
    doff = read_csv(doff_key)
    doff["date"] = pd.to_datetime(doff["date"])
    off = set(doff["date"].dt.date)
    M["is_day_off"] = pd.Series(M.index.date, index=M.index).isin(off).astype(int).values

    # 5) trim: where real load exists
    M = M[M["load_actual_mw"].notna()][COLS]
    M.index.name = "timestamp_local"

    # 6) write locally, then upload to data/processed/
    out_path = Path(__file__).parent / OUT_NAME
    M.to_csv(out_path, encoding="utf-8-sig")
    print(f"\n✅ built: {out_path.name}")
    print(f"   {len(M)} rows · {M.index.min()} → {M.index.max()} · tz={M.index.tz}")
    print(f"   columns: {list(M.columns)}")
    print(f"   weather read as UTC+3 (no DST) → converted to local; grid fully local")
    print(f"   NaN per column: {M.isna().sum().to_dict()}")

    if do_upload:
        upload_processed(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())