"""
feature_builder_1d.py — от часовия master → часов feature слой (forecast weather).
ЕДИНЕН скрипт: чете от S3 → пише/качва във S3.

  data/processed/master_hourly_long_forecasted_weather.csv  (часов master)
  + data/raw/load_actual            (ПЪЛНА товарна история за лаговете, lag8760)
  + data/raw/days_off               (празници/почивни)
        → features_1h_long.csv  (локално BG)  →  data/processed/

Блокове (огледало на forecast версията):
  LOAD     — лагове ≥24ч (lag24/48/168/336/720/8760), roll24/168_mean, diff_24_48, diff_24_168
  WEATHER  — прогноза за час T (temp_fc, temp²_fc, HDD/CDD_fc, ветрове, ghi/dni, cloud/precip/rh, HDD/CDD×dayoff)
  CALENDAR — 3 хармоники + workday, sindow/cosdow, sin/cos_doy, is_day_off, празнични

СТАЦИОНАРНОСТ: пуска ADF на товара и на Δ24. Ако нивото НЕ е стационарно → прави таргета
СТАЦИОНАРЕН чрез Δ24 (load_actual_mw = load − lag24) и пише features_1h_long_diff24.csv
(за реконструкция на нивото: load = lag24 + прогноза). Иначе пише нивовия features_1h_long.csv.

Usage:
    python feature_builder_1d.py            # build + push to data/processed (S3 default)
    python feature_builder_1d.py --local    # build locally only (no S3 upload)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
import warnings; warnings.filterwarnings("ignore")

# Make the shared tools/ dir importable (for upload_s3) from tools/features/layer_1/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from upload_s3 import read_csv, find_key, upload_processed  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LOCAL = "Europe/Sofia"
MASTER_KEY = "data/processed/master_hourly_long_forecasted_weather.csv"
NAME_LOAD_ACTUAL = "load_actual"
NAME_DAYS_OFF = "days_off"


def resolve(name: str) -> str:
    key = find_key(name)
    if key is None:
        raise SystemExit(f"No S3 object in data/raw/ matching name: {name!r}")
    return key


def main() -> int:
    do_upload = True  # always persist; backend (s3/local) chosen in upload_s3

    # ── вход 1: часов master (прогнозно метео + период) → локално ──
    print(f"master: {MASTER_KEY}")
    M = read_csv(MASTER_KEY, index_col=0)
    M.index = pd.to_datetime(M.index, utc=True).tz_convert(LOCAL)

    # ── вход 2: ПЪЛНА товарна история (raw) за лаговете (lag8760 без warmup) → локално ──
    la_key = resolve(NAME_LOAD_ACTUAL)
    print(f"load actual (raw): {la_key}")
    ra = read_csv(la_key)
    load = pd.Series(pd.to_numeric(ra["Actual Load"], errors="coerce").values,
                     index=pd.to_datetime(ra["timestamp"], utc=True)).sort_index()
    load = load[~load.index.duplicated(keep="first")].tz_convert(LOCAL)

    # ── календар ──
    doff_key = resolve(NAME_DAYS_OFF)
    print(f"days off (raw): {doff_key}")
    doff = read_csv(doff_key); doff["date"] = pd.to_datetime(doff["date"])
    holi = set(doff.loc[doff["is_holiday"].astype(int) == 1, "date"].dt.date); off = set(doff["date"].dt.date)

    idx = M.index                                                # изходна решетка = периодът на master-а
    loc = idx                                                    # вече локално
    h, dow, doy = loc.hour.values, loc.dayofweek.values, loc.dayofyear.values
    mon, day = loc.month.values, loc.day.values
    dates = pd.Series(loc.date, index=idx)
    is_off = dates.isin(off).astype(float).values; is_hol = dates.isin(holi).astype(float).values; iswd = 1-is_off

    F = pd.DataFrame(index=idx)
    F["load_actual_mw"] = load.reindex(idx)
    # ── LOAD (≥24ч, от пълна история) ──
    for lag in (24, 48, 168, 336, 720, 8760): F[f"lag{lag}"] = load.shift(lag).reindex(idx)
    F["roll24_mean"] = load.shift(24).rolling(24, min_periods=12).mean().reindex(idx)
    F["roll168_mean"] = load.shift(24).rolling(168, min_periods=84).mean().reindex(idx)
    F["diff_24_48"] = (load.shift(24)-load.shift(48)).reindex(idx)
    F["diff_24_168"] = (load.shift(24)-load.shift(168)).reindex(idx)
    # ── WEATHER = прогноза за T (от master, без shift) ──
    t = M["temp_c"]
    F["temp_fc"] = t; F["temp_sq_fc"] = t**2
    F["HDD_fc"] = (18-t).clip(lower=0); F["CDD_fc"] = (t-22).clip(lower=0)
    F["wind10_fc"] = M["wind10_ms"]; F["wind100_fc"] = M["wind100_ms"]
    F["ghi_fc"] = M["ghi_wm2"]; F["dni_fc"] = M["dni_wm2"]
    F["cloud_fc"] = M["cloud_pct"]; F["precip_fc"] = M["precip_mm"]; F["rh_fc"] = M["rh_pct"]
    F["HDD_x_dayoff"] = F["HDD_fc"]*is_off; F["CDD_x_dayoff"] = F["CDD_fc"]*is_off
    # ── CALENDAR ──
    for k in (1, 2, 3):
        s, c = np.sin(2*np.pi*k*h/24), np.cos(2*np.pi*k*h/24)
        F[f"sin{k}h"] = s; F[f"cos{k}h"] = c; F[f"sin{k}h_workday"] = s*iswd; F[f"cos{k}h_workday"] = c*iswd
    F["sindow"] = np.sin(2*np.pi*dow/7); F["cosdow"] = np.cos(2*np.pi*dow/7)
    F["sin_doy"] = np.sin(2*np.pi*doy/365.25); F["cos_doy"] = np.cos(2*np.pi*doy/365.25)
    F["is_day_off"] = is_off
    F["hol_easter_may"] = (((mon == 4) | (mon == 5)) & (day <= 10)).astype(float)*is_hol
    F["hol_other"] = np.clip(is_hol - F["hol_easter_may"].values, 0, 1)
    F["pre_holiday"] = np.array([1.0 if (pd.Timestamp(x)+pd.Timedelta(days=1)).date() in holi else 0.0 for x in dates])
    F["post_holiday"] = np.array([1.0 if (pd.Timestamp(x)-pd.Timedelta(days=1)).date() in holi else 0.0 for x in dates])
    F["bridge_day"] = np.array([1.0 if (x not in off and (pd.Timestamp(x)-pd.Timedelta(days=1)).date() in off
                                        and (pd.Timestamp(x)+pd.Timedelta(days=1)).date() in off) else 0.0 for x in dates])
    F.index.name = "timestamp_local"

    # ── СТАЦИОНАРНОСТ: ADF на товара и на Δ24; ако Δ24 стационарен → диференцирай таргета ──
    lvl = F["load_actual_mw"].dropna().values
    d24 = (F["load_actual_mw"] - F["lag24"]).dropna().values
    adf_lvl = adfuller(lvl, autolag="AIC")[1]
    adf_d24 = adfuller(d24, autolag="AIC")[1]
    print("СТАЦИОНАРНОСТ (само ADF; H0=има unit root, p<0.05 → стационарен):")
    print(f"   ниво (товар):        ADF p={adf_lvl:.4f}  → {'стационарен' if adf_lvl < 0.05 else 'НЕстационарен'}")
    print(f"   Δ24 (load − lag24):  ADF p={adf_d24:.4f}  → {'стационарен' if adf_d24 < 0.05 else 'НЕстационарен'}")

    STATIONARIZE = adf_lvl >= 0.05                               # правило: диференцирай САМО ако НИВОТО НЕ е стационарно
    if STATIONARIZE:
        F["load_actual_mw"] = F["load_actual_mw"] - F["lag24"]   # нивото НЕ е стационарно → Δ24; lag24 остава като КОТВА
        out_name = "features_1h_long_diff24.csv"
        note = ("⚠️ Нивото НЕ е стационарно → ТАРГЕТЪТ е ДИФЕРЕНЦИРАН: load_actual_mw = load − lag24.\n"
                "   Реконструкция на нивото: load = lag24 + прогноза.  Feature-ите остават нивови (предиктори).")
    else:
        out_name = "features_1h_long.csv"
        note = "Нивото Е стационарно (ADF) → БЕЗ диференциране (таргетът остава НИВО)."

    out_path = Path(__file__).parent / out_name
    F.to_csv(out_path, encoding="utf-8-sig")
    feats = [c for c in F.columns if c != "load_actual_mw"]
    print(f"\n✅ Записан: {out_path.name}")
    print(f"   {len(F)} реда · {F.index.min()} → {F.index.max()} · {len(feats)} feature-а · tz={F.index.tz}")
    print(f"   пълни редове (без NaN): {F[feats].dropna().shape[0]}")
    print(f"   {note}")

    if do_upload:
        upload_processed(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())