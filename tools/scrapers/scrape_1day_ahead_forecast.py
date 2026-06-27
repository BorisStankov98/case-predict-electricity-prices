#!/usr/bin/env python3
"""Fetch the 1-DAY-AHEAD hourly weather forecast for Bulgaria (country centroid),
in UTC, from the start of the archive through today, and write it to a single CSV.

This uses Open-Meteo's Previous Runs API
(https://previous-runs-api.open-meteo.com). The `_previous_day1` suffix returns,
for each valid timestamp, the value that was predicted exactly 24 hours earlier --
i.e. a true, fixed 1-day-ahead lead time (NOT the stitched best-available run, and
NOT ERA5 reanalysis/"real" data).

Availability of the full 9-variable set at a 1-day lead for this location begins
2024-02-17 (the 100 m wind series starts mid-day 2024-02-16), so that is the
archive start used here. Earlier dates only have temperature at a 1-day lead.
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
SOURCE = "open-meteo-1day-ahead"

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
# First date with the complete 9-variable set available at a 1-day lead.
ARCHIVE_START = date(2024, 2, 17)
LEAD_SUFFIX = "_previous_day1"  # value predicted 24 h before the valid time

# Base hourly variables (output column names). The request asks for each with the
# _previous_day1 suffix; the CSV is written with these clean names.
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
        chunk_end = min(date(cur.year, 12, 31), end)
        yield cur, chunk_end
        cur = date(cur.year + 1, 1, 1)


def fetch_chunk(start: date, end: date) -> dict:
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": ",".join(v + LEAD_SUFFIX for v in HOURLY_VARS),
        "timezone": "UTC",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    url = API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)


def main():
    today = datetime.now(timezone.utc).date()

    out_path = (
        f"bulgaria_1day_ahead_forecast_"
        f"{ARCHIVE_START.isoformat()}_{today.isoformat()}_UTC.csv"
    )
    total = 0

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)

        for start, end in year_chunks(ARCHIVE_START, today):
            print(f"Fetching {start} -> {end} ...", file=sys.stderr)
            data = fetch_chunk(start, end)
            hourly = data["hourly"]
            times = hourly["time"]  # e.g. "2024-02-17T00:00" -- already UTC

            for i, t in enumerate(times):
                ts = datetime.strptime(t, "%Y-%m-%dT%H:%M").strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                row = [ts] + [hourly[v + LEAD_SUFFIX][i] for v in HOURLY_VARS]
                row.append(SOURCE)
                writer.writerow(row)

            total += len(times)
            time.sleep(1)  # be polite to the free API

    print(f"Wrote {total} rows to {out_path}")

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
