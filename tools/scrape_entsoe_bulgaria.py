"""
ENTSO-E historical scraper for Bulgaria (BG bidding zone).

Pulls every Bulgaria-relevant dataset that's published on the ENTSO-E
Transparency Platform, going as far back as the data exists. Splits very
long ranges into yearly chunks so we don't trip ENTSO-E's per-request
size limit. Each call is wrapped in try/except — missing/empty datasets
are logged and skipped rather than killing the whole run.

Datasets fetched
----------------
Prices
  * Day-ahead prices (15-min from 2025-10-01, hourly before)
  * Intraday auction prices (IDA1/2/3) — 15-min, since ~Sep 2022
Load
  * Actual total load
  * Day-ahead load forecast
Generation
  * Actual Generation per Production Type
  * Installed Generation Capacity per Production Type (yearly nameplate MW)
  * Generation Forecast — day-ahead
  * Wind & solar forecast
Imbalance
  * Imbalance prices and volumes (often missing for BG, but tried)
Cross-border / coupling
  * Net position
  * Scheduled exchanges with each neighbour (RO, GR, RS, MK, TR)
  * Cross-border physical flows with each neighbour
  * Forecast transfer capacities (day-ahead, week-ahead, month-ahead,
    year-ahead) on each border
Outages
  * Unavailability of Generation Units
  * Unavailability of Production Units

Notes on what's NOT available
-----------------------------
The IBEX continuous-trading "weighted average price / max / min /
volume per QH" you saw on ibex.bg is NOT republished on ENTSO-E.
ENTSO-E only publishes auction (IDA) prices and aggregate volumes.
For SIDC continuous-trading per-MTU statistics you'd need the paid
EPEX/Nord Pool feed.

Pre-30-Sep-2022 data exists for most series but is hourly resolution
(Bulgaria didn't have 15-min products before then).

Setup
-----
    pip install entsoe-py pandas
    export ENTSOE_API_KEY="your-token-here"

Usage
-----
    python scrape_entsoe_bulgaria.py
        # default window: 2022-09-30 → today

    python scrape_entsoe_bulgaria.py 2024-01-01 2024-12-31

Outputs (all in ./entsoe_bg/):
  prices_day_ahead.csv
  prices_intraday_ida.csv
  load_actual.csv
  load_forecast_day_ahead.csv
  generation_per_type.csv
  installed_capacity_per_type.csv   (yearly, one row per year)
  generation_forecast_day_ahead.csv
  wind_solar_forecast.csv
  imbalance_prices.csv             (if available)
  imbalance_volumes.csv             (if available)
  net_position.csv                  (if available)
  scheduled_exchanges_<NB>.csv      one per neighbour where data exists
  physical_flows_<NB>.csv           one per neighbour where data exists
  transfer_capacity_<dayahead|weekahead|monthahead|yearahead>_<NB>.csv
  unavailability_generation_units.csv
  unavailability_production_units.csv
  _summary.json                     what worked, what didn't, row counts
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

try:
    from entsoe import EntsoePandasClient
    from entsoe.exceptions import NoMatchingDataError
except ImportError:
    sys.exit("Please install entsoe-py first:  pip install entsoe-py pandas")

BG = "BG"
TZ = "Europe/Sofia"
NEIGHBOURS = ["RO", "GR", "RS", "MK", "TR"]

# Hardcoded ENTSO-E API key. Paste your token between the quotes below.
# NOTE: this file is tracked by git — if you commit/push it, the key is
# exposed. Falls back to the ENTSOE_API_KEY env var when left blank.
ENTSOE_API_KEY = "b90aba93-81f7-4458-be2b-36cbe599bd95"

# Bulgaria 15-minute MTU intraday go-live (per ENTSO-E SIDC announcements).
# Earlier dates may still return hourly data; we let the API decide.
DEFAULT_START = date(2022, 9, 30)

OUT_DIR = Path(__file__).parent / "entsoe_bg"
OUT_DIR.mkdir(exist_ok=True)


# ---------- helpers ----------

def yearly_chunks(start: date, end: date):
    """Yield (chunk_start, chunk_end) pairs no larger than ~1 year.
    ENTSO-E rejects requests spanning more than 1 year for many endpoints."""
    cur = start
    while cur < end:
        nxt = min(date(cur.year + 1, cur.month, cur.day), end)
        yield cur, nxt
        cur = nxt


def to_ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(d.isoformat(), tz=TZ)


def safe_call(label: str, fn: Callable, *args, **kwargs):
    """Wrap an entsoe-py call; return None on no-data / known errors."""
    try:
        result = fn(*args, **kwargs)
        if result is None or (hasattr(result, "empty") and result.empty):
            print(f"    {label}: no data")
            return None
        return result
    except NoMatchingDataError:
        print(f"    {label}: no data (NoMatchingDataError)")
        return None
    except Exception as e:
        msg = str(e).split("\n")[0][:120]
        print(f"    {label}: ERROR  {type(e).__name__}: {msg}")
        return None


def fetch_chunked(label: str, fn: Callable, start: date, end: date,
                  *args, **kwargs) -> pd.DataFrame | None:
    """Call fn(start, end, *args, **kwargs) one year at a time and concat."""
    print(f"  • {label}")
    pieces: list = []
    for cs, ce in yearly_chunks(start, end):
        chunk = safe_call(f"  {cs} → {ce}", fn,
                          start=to_ts(cs), end=to_ts(ce), *args, **kwargs)
        if chunk is not None:
            if isinstance(chunk, pd.Series):
                chunk = chunk.to_frame(name=label)
            pieces.append(chunk)
        time.sleep(0.5)  # be polite to the API
    if not pieces:
        return None

    # The unavailability calls return event-style DataFrames with a
    # RangeIndex (not a DatetimeIndex). Concat works either way; only
    # the duplicate-trim assumes a unique time index.
    out = pd.concat(pieces)
    if isinstance(out.index, pd.DatetimeIndex):
        out = out.sort_index()
        out = out[~out.index.duplicated(keep="first")]
    else:
        out = out.reset_index(drop=True)
    return out


def save(name: str, df: pd.DataFrame | None, summary: dict) -> None:
    if df is None or df.empty:
        summary[name] = {"rows": 0, "ok": False}
        return

    # Reset DatetimeIndex into a 'timestamp' column; leave RangeIndex alone
    if isinstance(df.index, pd.DatetimeIndex):
        df_out = df.reset_index().rename(columns={"index": "timestamp"})
    else:
        df_out = df.copy()

    path = OUT_DIR / f"{name}.csv"
    df_out.to_csv(path, index=False)
    cols = list(df_out.columns)
    print(f"    ✓ {name}.csv  ({len(df_out):,} rows, {len(cols)} cols)")
    first_ts = str(df.index.min()) if isinstance(df.index, pd.DatetimeIndex) else None
    last_ts = str(df.index.max()) if isinstance(df.index, pd.DatetimeIndex) else None
    summary[name] = {
        "rows": len(df_out),
        "ok": True,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "columns": cols,
    }


# ---------- main scrape ----------

def run(start: date, end: date) -> dict:
    api_key = ENTSOE_API_KEY or os.environ.get("ENTSOE_API_KEY")
    if not api_key:
        sys.exit("Set ENTSOE_API_KEY (hardcode it at the top of this file "
                 "or export it in your environment) first.")
    client = EntsoePandasClient(api_key=api_key)

    print(f"Bulgaria (BG) on ENTSO-E,  {start}  →  {end}")
    print(f"Output: {OUT_DIR}/\n")

    summary: dict = {"window": {"start": str(start), "end": str(end)}}

    # ============ Prices ============
    print("Prices")
    df = fetch_chunked("prices_day_ahead",
                       client.query_day_ahead_prices, start, end, country_code=BG)
    save("prices_day_ahead", df, summary)

    # entsoe-py v0.8+ requires picking which IDA auction (1, 2, or 3) per call.
    # Loop over all three; concatenate so the output has one row per delivery
    # period per auction. We add an 'ida_sequence' column so the auctions can
    # be told apart downstream.
    ida_frames: list = []
    for seq in (1, 2, 3):
        df = fetch_chunked(f"prices_intraday_ida{seq}",
                           client.query_intraday_prices, start, end,
                           country_code=BG, sequence=seq)
        if df is not None and not df.empty:
            df = df.copy()
            df["ida_sequence"] = seq
            ida_frames.append(df)
    if ida_frames:
        ida_combined = pd.concat(ida_frames).sort_index()
    else:
        ida_combined = None
    save("prices_intraday_ida", ida_combined, summary)

    # ============ Load ============
    print("\nLoad")
    df = fetch_chunked("load_actual",
                       client.query_load, start, end, country_code=BG)
    save("load_actual", df, summary)

    # process_type='A01' = day-ahead. The default is A01 already, but
    # we set it explicitly for clarity.
    df = fetch_chunked("load_forecast_day_ahead",
                       client.query_load_forecast, start, end,
                       country_code=BG, process_type="A01")
    save("load_forecast_day_ahead", df, summary)

    # ============ Generation ============
    print("\nGeneration")
    df = fetch_chunked("generation_per_type",
                       client.query_generation, start, end,
                       country_code=BG, psr_type=None)
    save("generation_per_type", df, summary)

    # Installed (nameplate) capacity per production type. Published once a
    # year, so the output is one row per year with the same fuel-type columns
    # as generation_per_type — join on year to get a utilisation / capacity
    # factor (actual generation ÷ installed capacity) downstream.
    df = fetch_chunked("installed_capacity_per_type",
                       client.query_installed_generation_capacity, start, end,
                       country_code=BG, psr_type=None)
    save("installed_capacity_per_type", df, summary)

    df = fetch_chunked("generation_forecast_day_ahead",
                       client.query_generation_forecast, start, end,
                       country_code=BG)
    save("generation_forecast_day_ahead", df, summary)

    df = fetch_chunked("wind_solar_forecast",
                       client.query_wind_and_solar_forecast, start, end,
                       country_code=BG, psr_type=None)
    save("wind_solar_forecast", df, summary)

    # ============ Imbalance ============
    print("\nImbalance")
    df = fetch_chunked("imbalance_prices",
                       client.query_imbalance_prices, start, end, country_code=BG)
    save("imbalance_prices", df, summary)

    df = fetch_chunked("imbalance_volumes",
                       client.query_imbalance_volumes, start, end, country_code=BG)
    save("imbalance_volumes", df, summary)

    # ============ Cross-border / coupling ============
    print("\nCross-border / coupling")
    df = fetch_chunked("net_position",
                       client.query_net_position, start, end, country_code=BG)
    save("net_position", df, summary)

    for nb in NEIGHBOURS:
        # Scheduled exchanges (commercial schedules from market coupling)
        df = fetch_chunked(f"scheduled_exchanges_{BG}_to_{nb}",
                           client.query_scheduled_exchanges, start, end,
                           country_code_from=BG, country_code_to=nb)
        save(f"scheduled_exchanges_{BG}_to_{nb}", df, summary)
        df = fetch_chunked(f"scheduled_exchanges_{nb}_to_{BG}",
                           client.query_scheduled_exchanges, start, end,
                           country_code_from=nb, country_code_to=BG)
        save(f"scheduled_exchanges_{nb}_to_{BG}", df, summary)

        # Cross-border PHYSICAL flows (actual measured flows on the lines)
        df = fetch_chunked(f"physical_flows_{BG}_to_{nb}",
                           client.query_crossborder_flows, start, end,
                           country_code_from=BG, country_code_to=nb)
        save(f"physical_flows_{BG}_to_{nb}", df, summary)
        df = fetch_chunked(f"physical_flows_{nb}_to_{BG}",
                           client.query_crossborder_flows, start, end,
                           country_code_from=nb, country_code_to=BG)
        save(f"physical_flows_{nb}_to_{BG}", df, summary)

        # Forecast transfer capacities (NTC — Net Transfer Capacity).
        # ENTSO-E exposes these at four time horizons; not all borders
        # publish all horizons, so we try all four and skip what's empty.
        for horizon, fn in [
            ("dayahead", client.query_net_transfer_capacity_dayahead),
            ("weekahead", client.query_net_transfer_capacity_weekahead),
            ("monthahead", client.query_net_transfer_capacity_monthahead),
            ("yearahead", client.query_net_transfer_capacity_yearahead),
        ]:
            df = fetch_chunked(f"transfer_capacity_{horizon}_{BG}_to_{nb}",
                               fn, start, end,
                               country_code_from=BG, country_code_to=nb)
            save(f"transfer_capacity_{horizon}_{BG}_to_{nb}", df, summary)
            df = fetch_chunked(f"transfer_capacity_{horizon}_{nb}_to_{BG}",
                               fn, start, end,
                               country_code_from=nb, country_code_to=BG)
            save(f"transfer_capacity_{horizon}_{nb}_to_{BG}", df, summary)

    # ============ Outages / Unavailability ============
    # These return event-style DataFrames (one row per outage notification)
    # with start/end columns rather than a fixed-resolution time index.
    print("\nOutages / unavailability")
    df = fetch_chunked("unavailability_generation_units",
                       client.query_unavailability_of_generation_units,
                       start, end, country_code=BG)
    save("unavailability_generation_units", df, summary)

    df = fetch_chunked("unavailability_production_units",
                       client.query_unavailability_of_production_units,
                       start, end, country_code=BG)
    save("unavailability_production_units", df, summary)

    # ---- Summary ----
    summary_path = OUT_DIR / "_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nSummary written to {summary_path}")
    return summary


def main() -> int:
    today = date.today()
    start = DEFAULT_START
    end = today

    do_upload = "--upload" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--upload"]

    if len(args) == 2:
        start = datetime.strptime(args[0], "%Y-%m-%d").date()
        end = datetime.strptime(args[1], "%Y-%m-%d").date()
    elif len(args) not in (0, 2):
        sys.exit("Usage: python scrape_entsoe_bulgaria.py [START END] [--upload]\n"
                 "       (dates as YYYY-MM-DD; default = 2022-09-30 → today)")

    summary = run(start, end)

    if do_upload:
        from upload_s3 import upload
        upload(OUT_DIR)

    ok = [k for k, v in summary.items()
          if isinstance(v, dict) and v.get("ok")]
    missing = [k for k, v in summary.items()
               if isinstance(v, dict) and v.get("ok") is False]
    print("\n=== Done ===")
    print(f"  ✓ {len(ok)} datasets saved")
    print(f"  - {len(missing)} datasets had no data")
    if missing:
        print("    missing:", ", ".join(missing))
    print(f"  → all files in {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
