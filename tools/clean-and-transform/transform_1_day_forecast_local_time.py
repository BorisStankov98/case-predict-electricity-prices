"""
transform_1_day_forecast_local_time.py — reproducible 1-DAY master builder
(raw → canonical hourly master, day-ahead horizon).

Reads the raw inputs straight from S3 (data/raw/), builds the hourly master
with the day-ahead weather FORECAST for hour T, converts to local BG time, and
uploads the result to S3 data/processed/. S3 is the source of truth — no local
data file is.

Inputs (S3, data/raw/, located by stable name fragment so date/hour stamps and
sub-folder don't matter):
  load_actual                              (Actual Load)
  load_forecast_day_ahead                  (Forecasted Load — ESO day-ahead)
  1day_ahead_forecast (..._UTC.csv)        (forecasted weather; newest pick)
  days_off                                 (BG calendar; newest pick)
      → hourly master (load + ESO forecast + forecasted weather for T + is_day_off)
      → trimmed from 2024-02-17 (start of the weather forecast archive)
      → LOCAL BG time (Europe/Sofia, tz-aware)
      → data/processed/master_hourly_long_forecasted_weather.csv

Honestly: the weather is a day-ahead FORECAST for hour T (not actual, not
lagged). Load is tz-aware ENTSO-E; the weather forecast is naive-UTC (by file
name). Everything is canonicalised in UTC, then → local.

Usage:
    python transform_1_day_forecast_local_time.py            # build only (writes local CSV)
    python transform_1_day_forecast_local_time.py --upload   # build + upload to data/processed
"""
import sys
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
NAME_FORECAST_WX = "1day_ahead_forecast"
NAME_DAYS_OFF = "days_off"

# ---- output ----
OUT_NAME = "master_hourly_long_forecasted_weather.csv"
PROCESSED_KEY = f"data/processed/{OUT_NAME}"

CUT = pd.Timestamp("2024-02-17", tz="UTC")
LOCAL = "Europe/Sofia"
WMAP = {"temp_c": "temperature_2m", "wind10_ms": "wind_speed_10m", "wind100_ms": "wind_speed_100m",
        "wind_dir_100m_deg": "wind_direction_100m", "ghi_wm2": "shortwave_radiation",
        "dni_wm2": "direct_normal_irradiance", "cloud_pct": "cloud_cover",
        "precip_mm": "precipitation", "rh_pct": "relative_humidity_2m"}
COLS = ["load_actual_mw", "load_forecast_mw"] + list(WMAP.keys()) + ["is_day_off"]


def read_series(df, col, name):
    """tz-aware ENTSO-E OR naive-UTC → UTC-indexed numeric series."""
    ts = pd.to_datetime(df["timestamp"], utc=True)
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").values, index=ts, name=name)
    return s[~s.index.duplicated(keep="first")].sort_index()


def resolve(name: str) -> str:
    key = find_key(name)
    if key is None:
        raise SystemExit(f"No S3 object in data/raw/ matching name: {name!r}")
    return key


def main() -> int:
    do_upload = "--upload" in sys.argv

    # 1) load + ESO forecast (raw, from S3)
    la_key, lf_key = resolve(NAME_LOAD_ACTUAL), resolve(NAME_LOAD_FORECAST)
    print(f"load actual:   {la_key}")
    print(f"load forecast: {lf_key}")
    load_a = read_series(read_csv(la_key), "Actual Load", "load_actual_mw")
    load_f = read_series(read_csv(lf_key), "Forecasted Load", "load_forecast_mw")

    # 2) forecasted weather (raw, naive-UTC) — newest matching file
    fc_key = resolve(NAME_FORECAST_WX)
    print(f"weather forecast: {fc_key}")
    FC = read_csv(fc_key)
    FC.index = pd.to_datetime(FC["timestamp"], utc=True)
    FC = FC[~FC.index.duplicated(keep="first")].sort_index()

    # 3) hourly UTC grid = forecast period (weather defines the start 2024-02-17)
    grid = pd.date_range(max(CUT, FC.index.min()), FC.index.max(), freq="1h", tz="UTC")
    M = pd.DataFrame(index=grid)
    M["load_actual_mw"] = load_a.reindex(grid)
    M["load_forecast_mw"] = load_f.reindex(grid)
    for dst, src in WMAP.items():
        M[dst] = pd.to_numeric(FC[src], errors="coerce").reindex(grid)

    # 4) is_day_off by LOCAL date
    loc = M.index.tz_convert(LOCAL)
    doff_key = resolve(NAME_DAYS_OFF)
    print(f"days off: {doff_key}")
    doff = read_csv(doff_key)
    doff["date"] = pd.to_datetime(doff["date"])
    off = set(doff["date"].dt.date)
    M["is_day_off"] = pd.Series(loc.date, index=M.index).isin(off).astype(int).values

    # 5) trim: from CUT and where real load exists (end follows load availability)
    M = M[(M.index >= CUT) & M["load_actual_mw"].notna()][COLS]

    # 6) → local BG time
    M.index = M.index.tz_convert(LOCAL)
    M.index.name = "timestamp_local"

    # 7) compare with the current canonical in S3 (best-effort reproducibility check)
    try:
        old = read_csv(PROCESSED_KEY, index_col=0)
        old.index = pd.to_datetime(old.index, utc=True).tz_convert(LOCAL)
        common = M.index.intersection(old.index)
        same_cols = list(M.columns) == list(old.columns)
        maxdiff = (M.loc[common, "load_actual_mw"] - old.loc[common, "load_actual_mw"]).abs().max()
        print(f"vs current S3 file: rows new={len(M)} old={len(old)} · common={len(common)} · "
              f"cols match={same_cols} · max|Δload|={maxdiff:.4f}")
    except Exception:
        print("(no current canonical in S3 yet — first build)")

    # 8) write locally, then upload to data/processed/
    out_path = Path(__file__).parent / OUT_NAME
    M.to_csv(out_path, encoding="utf-8-sig")
    print(f"\n✅ built: {out_path.name}")
    print(f"   {len(M)} rows · {M.index.min()} → {M.index.max()} · tz={M.index.tz}")
    print(f"   columns: {list(M.columns)}")
    print(f"   (weather is a day-ahead FORECAST for T; index timestamp_local)")

    if do_upload:
        upload_processed(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())