#!/usr/bin/env python
"""End-to-end pipeline: raw CSVs -> joined dataset -> 3 layers -> metrics + plots.

Run from the repo root (or anywhere):

    python -m solution.run_pipeline
    # or
    python solution/run_pipeline.py

Outputs land in solution/outputs/:
    dataset_hourly.csv      the joined, analysis-ready hourly dataset
    metrics.json            full metrics (model vs baselines, every layer/horizon)
    metrics_summary.csv     flat table for quick reading
    figures/*.png           forecast-vs-actual and skill plots
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

# Allow running as a plain script (python solution/run_pipeline.py).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution.src import config as C
from solution.src import data as D
from solution.src import models as M


def _flatten(layer_label, hlabel, res) -> list[dict]:
    rows = []
    for which in ("model", "persistence", "seasonal_naive"):
        m = res[which]
        rows.append({
            "layer": layer_label, "horizon": hlabel, "estimator": which,
            "n": m["n"], "MAE": round(m["MAE"], 3), "RMSE": round(m["RMSE"], 3),
            "MAPE": round(m["MAPE"], 2), "sMAPE": round(m["sMAPE"], 2),
        })
    return rows


def _plot_test(layer, title, path):
    yte, pred, pers, _ = layer._test
    fig, ax = plt.subplots(figsize=(12, 4))
    show = slice(0, min(len(yte), 24 * 14))  # first ~2 weeks of test
    ax.plot(yte.index[show], yte.values[show], label="actual", lw=1.4)
    ax.plot(pred.index[show], pred.values[show], label="model", lw=1.1)
    ax.plot(pers.index[show], pers.values[show], label="persistence",
            lw=0.8, alpha=0.6)
    ax.set_title(title)
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _series_block(yte, pred, pers, seas, max_points=None):
    """Serialise a test-set forecast block for the HTML report."""
    if max_points:
        yte, pred, pers = yte.iloc[:max_points], pred.iloc[:max_points], pers.iloc[:max_points]
    def clean(s):
        return [None if pd.isna(v) else round(float(v), 2) for v in s.values]
    return {
        "t": [t.isoformat() for t in yte.index],
        "actual": clean(yte),
        "model": clean(pred),
        "persistence": clean(pers),
    }


def _correlations(df):
    """Pearson correlation of key drivers with each target, for the heatmap."""
    targets = ["load_mw", "gen_total_mw", "price_eur_per_mwh"]
    drivers = ["load_forecast_mw", "gen_forecast_mw", "gen_solar_mw",
               "gen_wind_onshore_mw", "gen_nuclear_mw",
               "gen_fossil_brown_coal_lignite_mw", "gen_fossil_gas_mw",
               "solar_forecast_mw", "wind_forecast_mw", "net_position_mw",
               "outage_unavail_mw", "wx_temperature_2m", "wx_wind_speed_100m",
               "wx_shortwave_radiation", "wx_cloud_cover", "wx_relative_humidity_2m",
               "net_flow_RO_mw", "net_flow_GR_mw", "net_flow_RS_mw",
               "net_flow_MK_mw", "net_flow_TR_mw"]
    drivers = [d for d in drivers if d in df.columns]
    cm = df[drivers + targets].corr().loc[drivers, targets]
    return {
        "drivers": [d.replace("_mw", "").replace("wx_", "wx:") for d in drivers],
        "targets": ["load", "generation", "price"],
        "matrix": [[None if pd.isna(v) else round(float(v), 2) for v in cm.loc[d]]
                   for d in drivers],
    }


def main():
    C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    C.FIGURE_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/4] Building joined hourly dataset ...")
    df = D.build_hourly_dataset()
    df.to_csv(C.OUTPUT_DIR / "dataset_hourly.csv")
    print(f"      {df.shape[0]} hourly rows x {df.shape[1]} cols, "
          f"{df.index.min()} .. {df.index.max()}")

    print("[2/4] Building & evaluating layers (24h, 1week) ...")
    layers = M.build_layers(df)
    all_metrics = {}
    flat_rows = []
    series = {}        # for the HTML report
    skill_rows = []
    for (lname, hlabel), (layer, season) in layers.items():
        res = layer.evaluate(season)
        key = f"{lname}_{hlabel}"
        all_metrics[key] = res
        flat_rows += _flatten(lname, hlabel, res)
        s = res["skill_vs_persistence_MAE"]
        alpha = getattr(layer, "alpha", float("nan"))
        print(f"      {lname:9s} {hlabel:6s}  MAE={res['model']['MAE']:8.2f} "
              f"| persist={res['persistence']['MAE']:8.2f} "
              f"| skill={s:+.3f} | alpha={alpha:.1f}")
        _plot_test(layer, f"{lname} {hlabel}: forecast vs actual",
                   C.FIGURE_DIR / f"{lname}_{hlabel}.png")
        series[key] = _series_block(*layer._test)
        skill_rows.append({"layer": lname, "horizon": hlabel,
                           "skill_vs_persistence": round(s, 3),
                           "skill_vs_seasonal": round(res["skill_vs_seasonal_MAE"], 3),
                           "alpha": alpha})

    print("[3/4] Evaluating 15-minute price horizon (native QH) ...")
    qh = D.load_price_qh()
    res_qh = M.evaluate_qh_price(qh)
    all_metrics["L3_price_15min"] = res_qh
    flat_rows += _flatten("L3_price", "15min", res_qh)
    print(f"      L3_price  15min   MAE={res_qh['model']['MAE']:8.2f} "
          f"| persist={res_qh['persistence']['MAE']:8.2f} "
          f"| skill={res_qh['skill_vs_persistence_MAE']:+.3f}")
    # First ~2 weeks of the QH test set keeps the chart readable & light.
    series["L3_price_15min"] = _series_block(*res_qh["_test"], max_points=96 * 14)
    skill_rows.append({"layer": "L3_price", "horizon": "15min",
                       "skill_vs_persistence": round(res_qh["skill_vs_persistence_MAE"], 3),
                       "skill_vs_seasonal": None,
                       "alpha": res_qh.get("alpha")})

    print("[4/4] Writing outputs ...")
    # metrics.json must be JSON-clean: drop the non-serialisable _test arrays.
    clean = {k: {kk: vv for kk, vv in v.items() if kk != "_test"}
             for k, v in all_metrics.items()}
    with open(C.OUTPUT_DIR / "metrics.json", "w") as f:
        json.dump(clean, f, indent=2)
    summary = pd.DataFrame(flat_rows)
    summary.to_csv(C.OUTPUT_DIR / "metrics_summary.csv", index=False)

    report_data = {
        "generated_at": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "dataset": {"rows": int(df.shape[0]), "cols": int(df.shape[1]),
                    "start": str(df.index.min()), "end": str(df.index.max())},
        "metrics": flat_rows,
        "skill": skill_rows,
        "series": series,
        "correlations": _correlations(df),
    }
    with open(C.OUTPUT_DIR / "report_data.json", "w") as f:
        json.dump(report_data, f)

    print("\n=== Test-set metrics (held-out tail) ===")
    print(summary.to_string(index=False))
    print(f"\nOutputs in: {C.OUTPUT_DIR}")


if __name__ == "__main__":
    main()
