"""
feature_builder_15min.py — feature слой за прогноза 1 ЧАС напред (nowcast → 15-мин рампа), БЕЗ look-ahead.
ЕДИНЕН скрипт: чете от S3 → пише/качва във S3. Огледало на feature_builder_1d.py, но с КЪСИ лагове.

Разлика спрямо 24ч (това е цялата идея):
  • Гейтът е T (стоим „сега" и знаем товара ДО текущия час), таргетът е T+1ч.
    → позволени са КЪСИ лагове: lag1 = текущата стойност (persistence, водещ сигнал), lag2/3, diff1.
  • Метеото е day-ahead ПРОГНОЗА за час T+1 — налично на гейта (издадено ден по-рано), честно.

Вход (S3):
  data/processed/master_hourly_long_forecasted_weather.csv   (СЪЩИЯ 1-дневен master — без отделен transform)
  data/raw/load_actual            (ПЪЛНА товарна история за лаговете; clean ако е наличен, иначе raw)
  data/raw/days_off               (празници/почивни)
Изход:
  features_1h_ahead_long.csv  (локално BG)  →  data/processed/

Usage:
    python feature_builder_15min.py            # build only (writes local CSV)
    python feature_builder_15min.py --upload   # build + upload to data/processed
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
import warnings; warnings.filterwarnings("ignore")

# Make the shared tools/ dir importable (for upload_s3) from this subfolder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from upload_s3 import read_csv, find_key, upload_processed  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LOCAL = "Europe/Sofia"
# Споделя 1-дневния master (същата прогноза за метеото; няма отделен 15-мин transform).
MASTER_KEY = "data/processed/master_hourly_long_forecasted_weather.csv"
NAME_DAYS_OFF = "days_off"
OUT_NAME = "features_1h_ahead_long.csv"


def resolve(name: str) -> str:
    key = find_key(name)
    if key is None:
        raise SystemExit(f"No S3 object in data/raw/ matching name: {name!r}")
    return key


def resolve_load() -> str:
    """Предпочети DST-почистения товар ако е качен; иначе ползвай raw load_actual
    (същия, който ползва 24ч feature builder-ът)."""
    key = find_key("load_actual_clean")
    if key:
        return key
    key = find_key("load_actual")
    if key is None:
        raise SystemExit("No load_actual* object in data/raw/")
    print(f"  (load_actual_clean не е намерен — ползвам raw {key})")
    return key


def main() -> int:
    do_upload = "--upload" in sys.argv

    # ── вход 1: часов master (прогнозно метео + период) → локално ──
    print(f"master: {MASTER_KEY}")
    M = read_csv(MASTER_KEY, index_col=0)
    M.index = pd.to_datetime(M.index, utc=True).tz_convert(LOCAL)

    # ── вход 2: ПЪЛНА товарна история (raw) за лаговете → локално ──
    la_key = resolve_load()
    print(f"load (raw): {la_key}")
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
    loc = idx
    h, dow, doy = loc.hour.values, loc.dayofweek.values, loc.dayofyear.values
    mon, day = loc.month.values, loc.day.values
    dates = pd.Series(loc.date, index=idx)
    is_off = dates.isin(off).astype(float).values; is_hol = dates.isin(holi).astype(float).values; iswd = 1-is_off

    F = pd.DataFrame(index=idx)
    F["load_actual_mw"] = load.reindex(idx)                      # таргет = товар в час T+1 (=целевия ред)
    # ── LOAD (КЪСИ лагове ≥1ч; гейт = T) ──
    F["lag1"] = load.shift(1).reindex(idx)                       # текущата стойност (persistence)
    F["lag2"] = load.shift(2).reindex(idx)
    F["lag3"] = load.shift(3).reindex(idx)
    F["lag24"] = load.shift(24).reindex(idx)                     # същия час вчера (ниво)
    F["lag168"] = load.shift(168).reindex(idx)                   # същия час преди седмица
    F["roll3_mean"] = load.shift(1).rolling(3, min_periods=2).mean().reindex(idx)
    F["roll24_mean"] = load.shift(1).rolling(24, min_periods=12).mean().reindex(idx)
    F["diff1"] = (load.shift(1)-load.shift(2)).reindex(idx)      # моментум (последна посока)
    F["diff24"] = (load.shift(1)-load.shift(25)).reindex(idx)    # текущо ниво спрямо вчера по същото време
    # ── WEATHER = прогноза за целевия час (от master, без shift) ──
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

    # ── СТАЦИОНАРНОСТ (само ADF, за документация; нивото е стационарно → БЕЗ диференциране) ──
    lvl = F["load_actual_mw"].dropna().values
    adf_lvl = adfuller(lvl, autolag="AIC")[1]
    print(f"СТАЦИОНАРНОСТ (ADF на товара): p={adf_lvl:.4f} → "
          f"{'стационарен (без диференциране)' if adf_lvl < 0.05 else 'НЕстационарен'}")

    out_path = Path(__file__).parent / OUT_NAME
    F.to_csv(out_path, encoding="utf-8-sig")
    feats = [c for c in F.columns if c != "load_actual_mw"]
    load_b = [c for c in feats if c.startswith(("lag", "roll", "diff"))]
    wx_b = [c for c in feats if c.endswith(("_fc", "_dayoff"))]
    cal_b = [c for c in feats if c not in load_b+wx_b]
    print(f"\n✅ Записан: {out_path.name}")
    print(f"   {len(F)} реда · {F.index.min()} → {F.index.max()} · {len(feats)} feature-а · tz={F.index.tz}")
    print(f"   [LOAD {len(load_b)}]: {', '.join(load_b)}")
    print(f"   [WEATHER {len(wx_b)}]: {', '.join(wx_b)}")
    print(f"   [CALENDAR {len(cal_b)}]: {', '.join(cal_b)}")
    print(f"   пълни редове (без NaN): {F[feats].dropna().shape[0]}")

    if do_upload:
        upload_processed(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())