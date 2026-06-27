"""
feature_builder_1w.py — L1 feature слой за прогноза 1 СЕДМИЦА напред (168ч), БЕЗ look-ahead.
ЕДИНЕН скрипт: чете от S3 → пише/качва във S3.

Вход : data/processed/master_1week_long.csv (load_actual + АКТУАЛНО метео + is_day_off; пълна история)
        data/raw/days_off (празници)
Изход: features_1week_long.csv  →  data/processed/

Честна рамка (гейт = 168ч преди T → знаем само до отпреди седмица):
  • LOAD: лагове ≥168ч; rolling/diff върху shift(168).
  • WEATHER: lag168 (миналоседмичен АКТУАЛ — day-ahead прогноза НЕ е налична 7д напред).
  • CALENDAR: детерминистичен.
  • БЕЗ lag24/48/72, БЕЗ метео-прогноза, БЕЗ ЕСО.

Usage:
    python feature_builder_1w.py            # build + push to data/processed (S3 default)
    python feature_builder_1w.py --local    # build locally only (no S3 upload)
"""
import sys
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
MASTER_KEY = "data/processed/master_1week_long.csv"
NAME_DAYS_OFF = "days_off"
OUT_NAME = "features_1week_long.csv"
L = 168


def resolve(name: str) -> str:
    key = find_key(name)
    if key is None:
        raise SystemExit(f"No S3 object in data/raw/ matching name: {name!r}")
    return key


def main() -> int:
    do_upload = True  # always persist; backend (s3/local) chosen in upload_s3

    print(f"master: {MASTER_KEY}")
    M = read_csv(MASTER_KEY, index_col=0)
    M.index = pd.to_datetime(M.index, utc=True).tz_convert(LOCAL)

    doff_key = resolve(NAME_DAYS_OFF)
    print(f"days off (raw): {doff_key}")
    d = read_csv(doff_key); d["date"] = pd.to_datetime(d["date"])
    holi = set(d.loc[d["is_holiday"].astype(int) == 1, "date"].dt.date)
    off = set(d["date"].dt.date)

    loc = M.index.tz_convert(LOCAL)
    h, dow, doy, mon, day = loc.hour.values, loc.dayofweek.values, loc.dayofyear.values, loc.month.values, loc.day.values
    dates = pd.Series(loc.date, index=M.index)
    is_off = dates.isin(off).astype(float).values
    is_hol = dates.isin(holi).astype(float).values
    iswd = 1 - is_off
    load = M["load_actual_mw"]

    F = pd.DataFrame(index=M.index)
    F["load_actual_mw"] = load                                    # таргет

    # ── 1) LOAD — лагове ≥168ч ──
    for lag in (168, 336, 504, 720, 8760): F[f"lag{lag}"] = load.shift(lag)
    F["roll168_mean"] = load.shift(168).rolling(168, min_periods=84).mean()
    F["diff_168_336"] = load.shift(168) - load.shift(336)

    # ── 2) WEATHER — lag168 (миналоседмичен актуал) ──
    t = M["temp_c"].shift(L)
    F["temp_lag168"] = t
    F["temp_sq_lag168"] = t**2
    F["HDD_lag168"] = (18 - t).clip(lower=0)
    F["CDD_lag168"] = (t - 22).clip(lower=0)
    for c in ["wind10_ms", "wind100_ms", "ghi_wm2", "dni_wm2", "cloud_pct", "precip_mm", "rh_pct"]:
        F[f"{c}_lag168"] = M[c].shift(L)
    F["HDD_x_dayoff"] = F["HDD_lag168"]*is_off
    F["CDD_x_dayoff"] = F["CDD_lag168"]*is_off

    # ── 3) CALENDAR — детерминистичен ──
    for k in (1, 2, 3):
        s, c = np.sin(2*np.pi*k*h/24), np.cos(2*np.pi*k*h/24)
        F[f"sin{k}h"] = s; F[f"cos{k}h"] = c
        F[f"sin{k}h_workday"] = s*iswd; F[f"cos{k}h_workday"] = c*iswd
    F["sindow"] = np.sin(2*np.pi*dow/7); F["cosdow"] = np.cos(2*np.pi*dow/7)
    F["sin_doy"] = np.sin(2*np.pi*doy/365.25); F["cos_doy"] = np.cos(2*np.pi*doy/365.25)
    F["is_day_off"] = is_off
    F["hol_easter_may"] = (((mon == 4) | (mon == 5)) & (day <= 10)).astype(float)*is_hol
    F["pre_holiday"] = np.array([1.0 if (pd.Timestamp(x)+pd.Timedelta(days=1)).date() in holi else 0.0 for x in dates])
    F["post_holiday"] = np.array([1.0 if (pd.Timestamp(x)-pd.Timedelta(days=1)).date() in holi else 0.0 for x in dates])

    out_path = Path(__file__).parent / OUT_NAME
    F.to_csv(out_path, encoding="utf-8-sig")
    feats = [c for c in F.columns if c != "load_actual_mw"]
    load_b = [c for c in feats if c.startswith(("lag", "roll", "diff"))]
    wx_b = [c for c in feats if c.endswith("_lag168") or c.endswith("_dayoff")]
    cal_b = [c for c in feats if c not in load_b+wx_b]
    print(f"\n✅ Записан: {out_path.name}  ({len(F)} реда × {len(feats)} feature-а)")
    print(f"  диапазон: {F.index.min()} → {F.index.max()}")
    print(f"  [LOAD {len(load_b)}]: {', '.join(load_b)}")
    print(f"  [WEATHER lag168 {len(wx_b)}]: {', '.join(wx_b)}")
    print(f"  [CALENDAR {len(cal_b)}]: {', '.join(cal_b)}")
    print(f"  пълни редове (без NaN): {F[feats].dropna().shape[0]} (warmup: lag8760 → 1г)")

    if do_upload:
        upload_processed(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())