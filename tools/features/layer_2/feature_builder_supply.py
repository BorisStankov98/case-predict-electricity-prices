"""
feature_builder_supply.py — Layer 2 (SUPPLY) feature builder.
Single script: reads the supply master from S3 → writes/uploads the model-ready
feature table to S3.

Input : data/processed/master_supply_long.csv (supply target + drivers, hourly, local BG)
Output: data/processed/features_supply_long.csv → data/processed/

The Layer 2 supply model is driven by contemporaneous **weather**, the
**calendar** (weekend / holiday), and **unavailability** dummies (planned
maintenance + unplanned outages). This stage selects those honest predictors
alongside the `supply` target and drops warm-up rows with missing values, so the
model stage gets a clean matrix. (Standardisation is done in the model stage, on
the train split only — same convention as Layer 1.)

Usage:
    python feature_builder_supply.py            # build + push to data/processed (S3 default)
    python feature_builder_supply.py --local    # build locally only (no S3 upload)
"""
import sys
from pathlib import Path

import pandas as pd

# Make the shared tools/ dir importable (for upload_s3) from tools/features/layer_2/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from upload_s3 import read_csv, upload_processed  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LOCAL = "Europe/Sofia"
MASTER_KEY = "data/processed/master_supply_long.csv"
OUT_NAME = "features_supply_long.csv"

TARGET = "supply"
WEATHER = ["temperature_2m", "wind_speed_10m", "wind_speed_100m", "wind_direction_100m",
           "shortwave_radiation", "direct_normal_irradiance", "cloud_cover",
           "precipitation", "relative_humidity_2m"]
DUMMIES = ["is_weekend", "is_holiday", "prod_maint", "gen_maint", "gen_outages"]
FEATURES = WEATHER + DUMMIES


def main() -> int:
    do_upload = True  # always persist; backend (s3/local) chosen in upload_s3

    print(f"master: {MASTER_KEY}")
    M = read_csv(MASTER_KEY, index_col=0)
    M.index = pd.to_datetime(M.index, utc=True).tz_convert(LOCAL)

    missing = [c for c in [TARGET] + FEATURES if c not in M.columns]
    if missing:
        raise SystemExit(f"master is missing expected columns: {missing}")

    F = M[[TARGET] + FEATURES].copy()
    before = len(F)
    F = F.dropna()                                   # drop warm-up / gap rows

    out_path = Path(__file__).parent / OUT_NAME
    F.to_csv(out_path, encoding="utf-8-sig")
    print(f"\n✅ Записан: {out_path.name}  ({len(F)} реда × {len(FEATURES)} feature-а)")
    print(f"  диапазон: {F.index.min()} → {F.index.max()}  (dropped {before - len(F)} непълни реда)")
    print(f"  [WEATHER {len(WEATHER)}]: {', '.join(WEATHER)}")
    print(f"  [DUMMIES {len(DUMMIES)}]: {', '.join(DUMMIES)}")

    if do_upload:
        upload_processed(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
