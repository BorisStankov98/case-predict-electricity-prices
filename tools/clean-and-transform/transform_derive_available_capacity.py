"""
Derive an hourly *available* generation capacity series per production type
for Bulgaria, by combining two ENTSO-E datasets already scraped into
./entsoe_bg/:

  * installed_capacity_per_type.csv      yearly nameplate MW per fuel type
  * unavailability_generation_units.csv  per-event outages (start/end, MW)

ENTSO-E only publishes installed (nameplate) capacity once a year, so a
finer-grained "installed capacity" series does not exist at the source.
What *does* vary within the year is how much of that nameplate is actually
online — driven by planned/forced outages. This script reconstructs that:

    available_capacity(type, hour)
        = nameplate(type, year_of_hour)
        - sum over outages of (nominal_power - avail_qty)
          for outages of that type active during that hour

The result is one row per hour with the same fuel-type columns as
generation_per_type.csv, so it joins 1:1 onto the generation/price timeline.

Usage
-----
    python derive_available_capacity.py
        # reads ./entsoe_bg/, writes ./entsoe_bg/available_capacity_per_type.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "entsoe_bg"
TZ = "Europe/Sofia"

INSTALLED = DATA_DIR / "installed_capacity_per_type.csv"
OUTAGES = DATA_DIR / "unavailability_generation_units.csv"
GENERATION = DATA_DIR / "generation_per_type.csv"
OUT = DATA_DIR / "available_capacity_per_type.csv"


def _to_local(series: pd.Series) -> pd.Series:
    """Parse a timestamp column with mixed DST offsets into one tz."""
    return pd.to_datetime(series, utc=True).dt.tz_convert(TZ)


def load_nameplate() -> pd.DataFrame:
    """Yearly nameplate capacity, indexed by year (int)."""
    df = pd.read_csv(INSTALLED)
    df["timestamp"] = _to_local(df["timestamp"])
    df["year"] = df["timestamp"].dt.year
    df = df.drop(columns=["timestamp"]).set_index("year")
    # Drop all-empty columns (fuel types BG never had); keep numeric MW cols.
    df = df.apply(pd.to_numeric, errors="coerce")
    return df.dropna(axis=1, how="all").fillna(0.0)


def build_hourly_index() -> pd.DatetimeIndex:
    """Hourly timeline spanning the generation series, in local tz."""
    gen = pd.read_csv(GENERATION)
    ts = _to_local(gen["timestamp"])
    start = ts.min().floor("h")
    end = ts.max().ceil("h")
    return pd.date_range(start, end, freq="h", tz=TZ)


def outage_mw_by_type(index: pd.DatetimeIndex,
                      fuel_cols: list[str]) -> pd.DataFrame:
    """For each hour and fuel type, total MW offline due to active outages."""
    ev = pd.read_csv(OUTAGES)
    ev["start"] = _to_local(ev["start"])
    ev["end"] = _to_local(ev["end"])
    ev["nominal_power"] = pd.to_numeric(ev["nominal_power"], errors="coerce")
    ev["avail_qty"] = pd.to_numeric(ev["avail_qty"], errors="coerce").fillna(0.0)
    ev = ev.dropna(subset=["nominal_power", "start", "end"])
    # MW lost = nameplate of the unit minus what stayed available during the
    # outage. Clip at 0 so a malformed row can't add negative "loss".
    ev["mw_lost"] = (ev["nominal_power"] - ev["avail_qty"]).clip(lower=0.0)
    ev = ev[ev["mw_lost"] > 0]

    out = pd.DataFrame(0.0, index=index, columns=fuel_cols)
    for _, e in ev.iterrows():
        ptype = e["plant_type"]
        if ptype not in out.columns:
            continue  # outage on a fuel type with no nameplate column; skip
        # Hours where the outage is active: [start, end)
        mask = (index >= e["start"]) & (index < e["end"])
        if mask.any():
            out.loc[mask, ptype] += e["mw_lost"]
    return out


def main() -> int:
    for p in (INSTALLED, OUTAGES, GENERATION):
        if not p.exists():
            raise SystemExit(f"Missing required input: {p}")

    nameplate = load_nameplate()
    index = build_hourly_index()
    fuel_cols = list(nameplate.columns)

    # Broadcast the yearly nameplate across each hour by the hour's year.
    years = pd.Index(index.year, name="year")
    # Reindex nameplate onto every hour's year (ffill covers any gap year).
    nameplate_h = nameplate.reindex(years).ffill().bfill()
    nameplate_h.index = index

    outage = outage_mw_by_type(index, fuel_cols)

    available = (nameplate_h - outage).clip(lower=0.0)
    available.index.name = "timestamp"

    available.reset_index().to_csv(OUT, index=False)

    # Report: which types actually see outages, and how much.
    affected = outage.sum().sort_values(ascending=False)
    affected = affected[affected > 0]
    print(f"✓ {OUT.name}  ({len(available):,} hourly rows, "
          f"{len(fuel_cols)} fuel columns)")
    print(f"  span: {index.min()} → {index.max()}")
    print("  outage-MW·hours by type (only types with outages):")
    for t, v in affected.items():
        peak = outage[t].max()
        print(f"    {t:<32} peak {peak:>7.0f} MW offline")
    untouched = [c for c in fuel_cols if c not in affected.index]
    if untouched:
        print("  no outages reported (available == nameplate): "
              + ", ".join(untouched))

    if "--upload" in sys.argv:
        import pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from upload_s3 import upload
        upload(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
