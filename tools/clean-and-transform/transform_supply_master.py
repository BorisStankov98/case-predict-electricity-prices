"""
transform_supply_master.py — Layer 2 (SUPPLY) master builder
(raw → canonical hourly master, local BG time).

Supply = total generation (sum of all production types) + net imports
(net_position). This is the data-engineering stage of Layer 2: it reads the raw
inputs straight from S3 (data/raw/), assembles everything on ONE canonical hourly
grid in LOCAL BG time (Europe/Sofia, tz-aware, DST), derives the outage dummies,
and uploads the master to S3 data/processed/. S3 is the source of truth — no
local data file is. (Counterpart to the Layer 1 transforms.)

Inputs (S3, data/raw/):
  entsoe_bg/generation_per_type   tz-aware; MW per production type (hourly)
  entsoe_bg/net_position          tz-aware; net imports MW (hourly, later 15-min)
  weather_bg_total                ACTUAL weather, naive UTC+3 (no DST)
  days_off                        BG calendar (is_weekend, is_holiday)
  entsoe_bg/unavailability_production_units   planned-maintenance events
  entsoe_bg/unavailability_generation_units   planned maintenance + unplanned outages

Output: data/processed/master_supply_long.csv — one row per hour, columns:
  supply (target) · <11 generation types> · net_position ·
  <9 weather columns> · is_weekend · is_holiday ·
  prod_maint · gen_maint · gen_outages

Source time zones (same handling as the Layer 1 1-week master):
  • generation / net_position : tz-aware (offset in the file) → tz_convert local.
  • weather_bg_total : naive, empirically a fixed UTC+3 without DST → localized
      as UTC+3, then converted to Europe/Sofia.

Usage:
    python transform_supply_master.py            # build + push to data/processed (S3 default)
    python transform_supply_master.py --local    # build locally only (no S3 upload)
"""
import sys
from datetime import timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Make the shared tools/ dir importable (for upload_s3) from this subfolder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from upload_s3 import read_csv, find_key, upload_processed  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LOCAL = "Europe/Sofia"
WX_TZ = timezone(timedelta(hours=3))           # weather_bg_total = naive UTC+3 (no DST)

# ---- S3 inputs (key fragments under data/raw/) ----
KEY_GEN = "entsoe_bg/generation_per_type"
KEY_NET = "entsoe_bg/net_position"
NAME_WEATHER = "weather_bg_total"
NAME_DAYS_OFF = "days_off"
KEY_UNAVAIL_PROD = "entsoe_bg/unavailability_production_units"
KEY_UNAVAIL_GEN = "entsoe_bg/unavailability_generation_units"

# Generation types summed into `supply` (also kept as individual columns).
GEN_COLS = ["Biomass", "Fossil Brown coal/Lignite", "Fossil Gas", "Fossil Hard coal",
            "Hydro Pumped Storage", "Hydro Run-of-river and poundage",
            "Hydro Water Reservoir", "Nuclear", "Solar", "Waste", "Wind Onshore"]
# Weather columns kept under their raw open-meteo names (the Layer 2 model uses these).
WX_COLS = ["temperature_2m", "wind_speed_10m", "wind_speed_100m", "wind_direction_100m",
           "shortwave_radiation", "direct_normal_irradiance", "cloud_cover",
           "precipitation", "relative_humidity_2m"]

OUT_NAME = "master_supply_long.csv"


def resolve(name: str) -> str:
    key = find_key(name)
    if key is None:
        raise SystemExit(f"No S3 object in data/raw/ matching name: {name!r}")
    return key


def to_local(series: pd.Series) -> pd.Series:
    """Parse a tz-aware timestamp column (mixed DST offsets) into local BG time."""
    return pd.to_datetime(series, utc=True).dt.tz_convert(LOCAL)


def outage_flag(ev: pd.DataFrame, grid: pd.DatetimeIndex, businesstype: str) -> np.ndarray:
    """Hourly 0/1 flag: is an event of `businesstype` active during each hour?

    An event is active over [start, end]. Returns one int per grid hour.
    """
    sel = ev[ev["businesstype"] == businesstype]
    starts = to_local(sel["start"])
    ends = to_local(sel["end"])
    flag = np.zeros(len(grid), dtype=int)
    for st, en in zip(starts, ends):
        if pd.isna(st) or pd.isna(en):
            continue
        flag |= ((grid >= st) & (grid <= en))
    return flag.astype(int)


def main() -> int:
    do_upload = True  # always persist; backend (s3/local) chosen in upload_s3

    # 1) generation per type (tz-aware → local), summed; net imports reindexed hourly
    gen_key, net_key = resolve(KEY_GEN), resolve(KEY_NET)
    print(f"generation: {gen_key}")
    print(f"net imports: {net_key}")
    gen = read_csv(gen_key)
    gen.index = to_local(gen["timestamp"])
    gen = gen[~gen.index.duplicated(keep="first")].sort_index()
    for c in GEN_COLS:
        gen[c] = pd.to_numeric(gen[c], errors="coerce")
    gen[GEN_COLS] = gen[GEN_COLS].ffill()      # only hydro storage ever has gaps

    net = read_csv(net_key)
    net.index = to_local(net["timestamp"])
    net = net[~net.index.duplicated(keep="first")].sort_index()
    net_s = pd.to_numeric(net["net_position"], errors="coerce")

    # 2) canonical LOCAL hourly grid spanning the generation series
    grid = pd.date_range(gen.index.min().floor("h"), gen.index.max().ceil("h"),
                         freq="1h", tz=LOCAL)
    M = pd.DataFrame(index=grid)
    for c in GEN_COLS:
        M[c] = gen[c].reindex(grid)
    M[GEN_COLS] = M[GEN_COLS].ffill()
    M["net_position"] = net_s.reindex(grid).ffill()     # 15-min part → take the hour
    M["supply"] = M[GEN_COLS].sum(axis=1) + M["net_position"]

    # 3) ACTUAL weather (naive UTC+3 → local)
    wx_key = resolve(NAME_WEATHER)
    print(f"weather: {wx_key}")
    WX = read_csv(wx_key)
    WX.index = pd.DatetimeIndex(pd.to_datetime(WX["timestamp"])).tz_localize(WX_TZ).tz_convert(LOCAL)
    WX = WX[~WX.index.duplicated(keep="first")].sort_index()
    for c in WX_COLS:
        M[c] = pd.to_numeric(WX[c], errors="coerce").reindex(grid)
    M[WX_COLS] = M[WX_COLS].ffill()

    # 4) calendar dummies by LOCAL date
    doff_key = resolve(NAME_DAYS_OFF)
    print(f"days off: {doff_key}")
    doff = read_csv(doff_key)
    doff["date"] = pd.to_datetime(doff["date"]).dt.date
    weekend = dict(zip(doff["date"], doff["is_weekend"].astype(int)))
    holiday = dict(zip(doff["date"], doff["is_holiday"].astype(int)))
    dts = pd.Series(M.index.date, index=M.index)
    M["is_weekend"] = dts.map(weekend).fillna(0).astype(int).values
    M["is_holiday"] = dts.map(holiday).fillna(0).astype(int).values

    # 5) unavailability dummies (interval overlap onto the hourly grid)
    up_key, ug_key = resolve(KEY_UNAVAIL_PROD), resolve(KEY_UNAVAIL_GEN)
    print(f"unavailability (production): {up_key}")
    print(f"unavailability (generation): {ug_key}")
    up = read_csv(up_key)
    ug = read_csv(ug_key)
    M["prod_maint"] = outage_flag(up, grid, "Planned maintenance")
    M["gen_maint"] = outage_flag(ug, grid, "Planned maintenance")
    M["gen_outages"] = outage_flag(ug, grid, "Unplanned outage")

    # 6) order columns: target first, then drivers; trim to where supply exists
    cols = ["supply"] + GEN_COLS + ["net_position"] + WX_COLS + \
           ["is_weekend", "is_holiday", "prod_maint", "gen_maint", "gen_outages"]
    M = M[M["supply"].notna()][cols]
    M.index.name = "timestamp_local"

    out_path = Path(__file__).parent / OUT_NAME
    M.to_csv(out_path, encoding="utf-8-sig")
    print(f"\n✅ built: {out_path.name}")
    print(f"   {len(M)} rows · {M.index.min()} → {M.index.max()} · tz={M.index.tz}")
    print(f"   supply mean={M['supply'].mean():.0f} MW · "
          f"maint hours: prod={int(M['prod_maint'].sum())}, "
          f"gen={int(M['gen_maint'].sum())}, outages={int(M['gen_outages'].sum())}")
    print(f"   NaN per column: {M.isna().sum().to_dict()}")

    if do_upload:
        upload_processed(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
