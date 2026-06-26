#!/usr/bin/env python3
"""Fetch the archived hourly *forecast* for Bulgaria (country centroid) in UTC,
from 2022-01-01 through today, and write it to a single CSV.

This uses Open-Meteo's Historical Forecast API
(https://historical-forecast-api.open-meteo.com), which stores the forecasts that
were actually issued by the weather models in the past -- NOT the ERA5 reanalysis
("real"/measured) data. The archive begins 2022-01-01.

Requests are chunked by calendar year to keep each response a manageable size.
"""

import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

# Geographic centroid of Bulgaria
LATITUDE = 42.73
LONGITUDE = 25.49
SOURCE = "open-meteo-historical-forecast"

API = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ARCHIVE_START = date(2022, 1, 1)

# Hourly variables requested (order matters). "timestamp" and "source" are added
# by us; the rest are Open-Meteo hourly variables.
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


def year_chunks(start: date, end: date):
    """Yield (start_date, end_date) pairs split on calendar-year boundaries."""
    cur = start
    while cur <= end:
        year_end = date(cur.year, 12, 31)
        chunk_end = min(year_end, end)
        yield cur, chunk_end
        cur = date(cur.year + 1, 1, 1)


def fetch_chunk(start: date, end: date) -> dict:
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": ",".join(HOURLY_VARS),
        "timezone": "UTC",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    url = API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)


def main():
    today = datetime.now(timezone.utc).date()

    out_path = f"bulgaria_historical_forecast_2022_{today.isoformat()}_UTC.csv"
    total = 0

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)

        for start, end in year_chunks(ARCHIVE_START, today):
            print(f"Fetching {start} -> {end} ...", file=sys.stderr)
            data = fetch_chunk(start, end)
            hourly = data["hourly"]
            times = hourly["time"]  # e.g. "2022-01-01T00:00" -- already UTC

            for i, t in enumerate(times):
                ts = datetime.strptime(t, "%Y-%m-%dT%H:%M").strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                row = [ts] + [hourly[v][i] for v in HOURLY_VARS] + [SOURCE]
                writer.writerow(row)

            total += len(times)
            time.sleep(1)  # be polite to the free API

    print(f"Wrote {total} rows to {out_path}")

    if "--upload" in sys.argv:
        import pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from upload_s3 import upload
        upload(out_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
