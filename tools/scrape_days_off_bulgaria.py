"""
Bulgarian "days off" calendar generator.

Emits one row per calendar day with flags for weekends and official
public holidays (including the substitute days off that Bulgaria grants
when a fixed holiday falls on a weekend — the `holidays` package models
these as "(почивен ден)" entries).

Holiday dates — including the moving Orthodox Easter — come from the
`holidays` library, which computes them per year, so this stays correct
across the whole span instead of relying on a hand-maintained list.

Setup
-----
    pip install holidays pandas

Usage
-----
    python scrape_days_off_bulgaria.py
        # default span: 2022-01-01 → 2026-12-31

    python scrape_days_off_bulgaria.py 2022-01-01 2026-06-22

Output
------
    ./days_off_bg_<start>_<end>.csv   (one row per day OFF — weekdays dropped)
    columns: date, day_name, is_weekend, is_holiday
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import holidays
import pandas as pd

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday"]


def build(start: date, end: date) -> pd.DataFrame:
    years = range(start.year, end.year + 1)
    bg = holidays.Bulgaria(years=years, language="en_US")

    days = pd.date_range(start, end, freq="D")
    df = pd.DataFrame({"date": days})
    dow = df["date"].dt.dayofweek

    df["day_name"] = dow.map(lambda i: DAY_NAMES[i])
    df["is_weekend"] = (dow >= 5).astype(int)
    df["is_holiday"] = df["date"].dt.date.map(lambda d: d in bg).astype(int)
    df["is_off_day"] = ((df["is_weekend"] == 1) | (df["is_holiday"] == 1)).astype(int)

    df["date"] = df["date"].dt.strftime("%Y-%m-%d")

    # Keep only days off (weekends + public holidays); drop normal weekdays.
    # is_off_day is then constant (all 1), so drop it as redundant.
    df = df[df["is_off_day"] == 1].drop(columns="is_off_day").reset_index(drop=True)
    return df


def main() -> int:
    start = date(2022, 1, 1)
    end = date(2026, 12, 31)

    do_upload = "--upload" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--upload"]

    if len(args) == 2:
        start = datetime.strptime(args[0], "%Y-%m-%d").date()
        end = datetime.strptime(args[1], "%Y-%m-%d").date()
    elif len(args) not in (0, 2):
        sys.exit("Usage: python scrape_days_off_bulgaria.py [START END] [--upload]\n"
                 "       (dates as YYYY-MM-DD; default = 2022-01-01 → 2026-12-31)")

    df = build(start, end)

    out = Path(__file__).parent / f"days_off_bg_{start}_{end}.csv"
    df.to_csv(out, index=False)

    n_hol = int(df["is_holiday"].sum())
    print(f"Bulgaria days off,  {start}  →  {end}")
    print(f"  ✓ {len(df):,} off-days written → {out}  (weekends + holidays)")
    print(f"  public holidays (incl. substitutes): {n_hol:,}")

    if do_upload:
        from upload_s3 import upload
        upload(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())