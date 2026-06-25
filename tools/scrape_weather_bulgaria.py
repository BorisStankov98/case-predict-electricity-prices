"""
Open-Meteo weather scraper for Bulgaria.

Pulls hourly weather data from 2026-02-01 onwards for:
  - Five major cities: Sofia, Plovdiv, Varna, Burgas, Ruse
  - A "country average" series (mean of the five city series, weighted
    equally; close enough to a population-weighted average for energy
    modelling)

Variables fetched (hourly):
  temperature_2m              (°C)
  wind_speed_10m              (km/h)
  wind_speed_100m             (km/h)
  wind_direction_100m         (°)
  shortwave_radiation         (W/m², ~ GHI)
  direct_normal_irradiance    (W/m²)
  cloud_cover                 (%)
  precipitation               (mm)
  relative_humidity_2m        (%)

Source
------
Open-Meteo, free public API, no token required.
  - Archive endpoint (ERA5 reanalysis) for the bulk of the window;
    ERA5 lags real time by ~5 days.
  - Historical Forecast endpoint (high-resolution model archive) for
    the most recent days, to fill the ERA5 gap.
The script stitches the two sources transparently.

Note on ECMWF / ERA5 attribution
--------------------------------
The Archive endpoint surfaces ECMWF's ERA5 reanalysis (Copernicus).
If you publish anything based on this data, cite:
  Hersbach, H. et al. (2023). ERA5 hourly data on single levels from
  1940 to present. Copernicus Climate Change Service (C3S) Climate
  Data Store (CDS). DOI: 10.24381/cds.adbb2d47

Setup
-----
    pip install requests pandas

Usage
-----
    python scrape_weather_bulgaria.py
        # default window: 2026-02-01 → today

    python scrape_weather_bulgaria.py 2026-02-01 2026-05-09

Outputs (all in ./weather_bg/):
    weather_<city>.csv             one per city — ERA5 actuals (+ recent
                                   historical-forecast tail to fill the lag)
    weather_bg_total.csv           country-average actuals
    weather_<city>_forecast.csv    one per city — historical-forecast model
                                   archive across the FULL window (look-ahead
                                   safe feature: "what a forecast would show")
    weather_bg_total_forecast.csv  country-average forecast
    _summary.json                  actuals: per-location row counts / ranges
    _summary_forecast.json         forecast: per-location row counts / ranges

Actuals vs forecast
-------------------
The plain weather_<city>.csv files are ERA5 reanalysis — the *actual*
weather. Using them as model features causes look-ahead bias, because at
prediction time you only know the *forecast*. The *_forecast.csv files pull
Open-Meteo's Historical Forecast archive over the whole window so you have a
leakage-safe forecast feature. Note this archive is a rolling concatenation
of recent model runs, not a strictly-defined D-1 forecast; it only reaches
back to ~2022-01-01.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

ARCHIVE_URL  = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# Open-Meteo's Historical Forecast archive does not reach earlier than this.
FORECAST_ARCHIVE_START = date(2022, 1, 1)

CITIES = {
    # name      (latitude, longitude)
    "Sofia":   (42.6977, 23.3219),
    "Plovdiv": (42.1354, 24.7453),
    "Varna":   (43.2141, 27.9147),
    "Burgas":  (42.5048, 27.4626),
    "Ruse":    (43.8564, 25.9657),
}

VARIABLES = [
    "temperature_2m",
    "wind_speed_10m",
    "wind_speed_100m",
    "wind_direction_100m",
    "shortwave_radiation",
    "direct_normal_irradiance",
    "cloud_cover",
    "precipitation",
    "relative_humidity_2m",
]

TZ = "Europe/Sofia"
OUT_DIR = Path(__file__).parent / "weather_bg"
OUT_DIR.mkdir(exist_ok=True)


# ---------- helpers ----------

def fetch(endpoint: str, lat: float, lon: float,
          start: date, end: date) -> pd.DataFrame | None:
    """Hit an Open-Meteo endpoint for one city and one date range."""
    params = {
        "latitude":   lat,
        "longitude":  lon,
        "start_date": start.isoformat(),
        "end_date":   end.isoformat(),
        "hourly":     ",".join(VARIABLES),
        "timezone":   TZ,
        "wind_speed_unit": "kmh",
    }
    try:
        r = requests.get(endpoint, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"    ! HTTP error: {e}")
        return None

    hourly = data.get("hourly")
    if not hourly or "time" not in hourly:
        return None

    df = pd.DataFrame(hourly)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()
    return df


def fetch_city(name: str, lat: float, lon: float,
               start: date, end: date) -> pd.DataFrame | None:
    """Fetch a city's full window, stitching ERA5 archive + recent
    historical-forecast data to cover the ~5-day ERA5 lag."""
    today = date.today()
    archive_end = today - timedelta(days=6)        # safe ERA5 cutoff
    forecast_start = archive_end + timedelta(days=1)

    pieces: list[pd.DataFrame] = []

    # --- Archive (ERA5) for the bulk of the window ---
    if start <= archive_end:
        a_end = min(end, archive_end)
        print(f"  • {name}: archive  {start} → {a_end}")
        df = fetch(ARCHIVE_URL, lat, lon, start, a_end)
        if df is not None and not df.empty:
            df["source"] = "archive_era5"
            pieces.append(df)

    # --- Historical Forecast for the last few days ---
    if end >= forecast_start:
        f_start = max(start, forecast_start)
        print(f"  • {name}: forecast {f_start} → {end}")
        df = fetch(FORECAST_URL, lat, lon, f_start, end)
        if df is not None and not df.empty:
            df["source"] = "historical_forecast"
            pieces.append(df)

    if not pieces:
        return None
    out = pd.concat(pieces).sort_index()
    # If the two sources overlap by an hour, prefer the archive (first)
    out = out[~out.index.duplicated(keep="first")]
    return out


def fetch_city_forecast(name: str, lat: float, lon: float,
                        start: date, end: date) -> pd.DataFrame | None:
    """Fetch a city's full window from the Historical Forecast endpoint only.

    Unlike fetch_city (which stitches ERA5 actuals + a recent forecast tail),
    this pulls the model-forecast archive across the WHOLE window — the
    leakage-safe feature. The archive only reaches back to ~2022-01-01, so we
    clamp the start there.

    A single multi-year request to this endpoint times out, so we fetch it in
    yearly chunks and concatenate."""
    f_start = max(start, FORECAST_ARCHIVE_START)
    pieces: list[pd.DataFrame] = []
    cur = f_start
    while cur <= end:
        chunk_end = min(date(cur.year, 12, 31), end)
        print(f"  • {name}: forecast {cur} → {chunk_end}")
        df = fetch(FORECAST_URL, lat, lon, cur, chunk_end)
        if df is not None and not df.empty:
            pieces.append(df)
        time.sleep(0.3)  # be polite between chunks
        cur = date(cur.year + 1, 1, 1)

    if not pieces:
        return None
    out = pd.concat(pieces).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    out["source"] = "historical_forecast"
    return out


def make_country_average(per_city: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Average each variable across cities (equal weighting). Wind-direction
    averaging via vector mean to avoid the 359°/1° wrap-around bug."""
    import numpy as np

    if not per_city:
        return pd.DataFrame()

    aligned: dict[str, pd.DataFrame] = {}
    for var in VARIABLES:
        cols = []
        for city, df in per_city.items():
            if var in df.columns:
                s = df[var].rename(city)
                cols.append(s)
        if cols:
            aligned[var] = pd.concat(cols, axis=1)

    out = pd.DataFrame(index=next(iter(aligned.values())).index)

    for var, df in aligned.items():
        if var == "wind_direction_100m":
            # Need wind speed at 100m to do a proper vector mean
            spd_df = aligned.get("wind_speed_100m")
            if spd_df is None:
                # Fallback: scalar circular mean
                rad = np.deg2rad(df.values)
                u = np.cos(rad).mean(axis=1)
                v = np.sin(rad).mean(axis=1)
            else:
                rad = np.deg2rad(df.values)
                spd = spd_df.reindex(columns=df.columns).values
                u = (spd * np.cos(rad)).mean(axis=1)
                v = (spd * np.sin(rad)).mean(axis=1)
            ang = np.rad2deg(np.arctan2(v, u)) % 360
            out[var] = ang
        else:
            out[var] = df.mean(axis=1)

    out["source"] = "city_average"
    return out


def save(name: str, df: pd.DataFrame | None, summary: dict) -> None:
    if df is None or df.empty:
        summary[name] = {"rows": 0, "ok": False}
        return
    df_out = df.reset_index().rename(columns={"time": "timestamp"})
    path = OUT_DIR / f"{name}.csv"
    df_out.to_csv(path, index=False)
    print(f"    ✓ {name}.csv  ({len(df_out):,} rows)")
    summary[name] = {
        "rows":  len(df_out),
        "ok":    True,
        "first_timestamp": str(df.index.min()),
        "last_timestamp":  str(df.index.max()),
        "columns": list(df_out.columns),
    }


# ---------- main ----------

def run(start: date, end: date) -> dict:
    print(f"Bulgaria weather,  {start}  →  {end}")
    print(f"Output: {OUT_DIR}/\n")

    summary: dict = {"window": {"start": str(start), "end": str(end)}}
    per_city: dict[str, pd.DataFrame] = {}

    print("Cities")
    for city, (lat, lon) in CITIES.items():
        df = fetch_city(city, lat, lon, start, end)
        save(f"weather_{city}", df, summary)
        if df is not None and not df.empty:
            per_city[city] = df.drop(columns=["source"], errors="ignore")
        time.sleep(0.5)  # be polite

    print("\nCountry average")
    avg = make_country_average(per_city)
    save("weather_bg_total", avg, summary)

    # ---- Full-window FORECAST series (leakage-safe feature) ----
    # The forecast outputs get their own summary file so the two scrapes
    # (actuals vs forecast archive) can be tracked independently.
    print("\nForecast (historical-forecast archive, full window)")
    summary_fc: dict = {"window": {"start": str(start), "end": str(end)}}
    per_city_fc: dict[str, pd.DataFrame] = {}
    for city, (lat, lon) in CITIES.items():
        df = fetch_city_forecast(city, lat, lon, start, end)
        save(f"weather_{city}_forecast", df, summary_fc)
        if df is not None and not df.empty:
            per_city_fc[city] = df.drop(columns=["source"], errors="ignore")
        time.sleep(0.5)  # be polite

    print("\nCountry average (forecast)")
    avg_fc = make_country_average(per_city_fc)
    if not avg_fc.empty:
        avg_fc["source"] = "city_average_forecast"
    save("weather_bg_total_forecast", avg_fc, summary_fc)

    summary_path = OUT_DIR / "_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nSummary written to {summary_path}")

    summary_fc_path = OUT_DIR / "_summary_forecast.json"
    summary_fc_path.write_text(json.dumps(summary_fc, indent=2, default=str))
    print(f"Forecast summary written to {summary_fc_path}")
    return summary


def main() -> int:
    today = date.today()
    start = date(2026, 2, 1)
    end = today

    do_upload = "--upload" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--upload"]

    if len(args) == 2:
        start = datetime.strptime(args[0], "%Y-%m-%d").date()
        end = datetime.strptime(args[1], "%Y-%m-%d").date()
    elif len(args) not in (0, 2):
        sys.exit("Usage: python scrape_weather_bulgaria.py [START END] [--upload]\n"
                 "       (dates as YYYY-MM-DD; default = 2026-02-01 → today)")

    summary = run(start, end)

    if do_upload:
        from upload_s3 import upload
        upload(OUT_DIR)

    ok = sum(1 for k, v in summary.items()
             if isinstance(v, dict) and v.get("ok"))
    missing = [k for k, v in summary.items()
               if isinstance(v, dict) and v.get("ok") is False]
    print("\n=== Done ===")
    print(f"  ✓ {ok} files saved")
    if missing:
        print(f"  - missing: {', '.join(missing)}")
    print(f"  → all files in {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
