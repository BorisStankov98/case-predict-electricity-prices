#!/usr/bin/env python3
"""Fetch tomorrow's hourly weather forecast for Bulgaria (country centroid) in UTC
and write it to a CSV. Data source: Open-Meteo (https://open-meteo.com)."""

import csv
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# Geographic centroid of Bulgaria
LATITUDE = 42.73
LONGITUDE = 25.49
SOURCE = "open-meteo"

# Columns requested (order matters). "timestamp" and "source" are added by us;
# the rest are Open-Meteo hourly variables.
HOURLY_VARS = [
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
CSV_COLUMNS = ["timestamp"] + HOURLY_VARS + ["source"]


def main():
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": ",".join(HOURLY_VARS),
        "timezone": "UTC",
        "start_date": tomorrow,
        "end_date": tomorrow,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)

    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)

    hourly = data["hourly"]
    times = hourly["time"]  # e.g. "2026-06-24T00:00" — already UTC

    out_path = f"bulgaria_forecast_{tomorrow}_UTC.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for i, t in enumerate(times):
            # Timestamp in UTC, format: 2022-01-01 00:00:00
            ts = datetime.strptime(t, "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M:%S")
            row = [ts] + [hourly[v][i] for v in HOURLY_VARS] + [SOURCE]
            writer.writerow(row)

    print(f"Wrote {len(times)} rows to {out_path}")

    import pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from upload_s3 import upload
    upload(out_path)  # persists to the active backend (s3/local — see upload_s3)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
