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
    for (lname, hlabel), (layer, season) in layers.items():
        res = layer.evaluate(season)
        all_metrics[f"{lname}_{hlabel}"] = res
        flat_rows += _flatten(lname, hlabel, res)
        s = res["skill_vs_persistence_MAE"]
        print(f"      {lname:9s} {hlabel:6s}  MAE={res['model']['MAE']:8.2f} "
              f"| persist={res['persistence']['MAE']:8.2f} "
              f"| skill={s:+.3f} | alpha={getattr(layer, 'alpha', float('nan')):.1f}")
        _plot_test(layer, f"{lname} {hlabel}: forecast vs actual",
                   C.FIGURE_DIR / f"{lname}_{hlabel}.png")

    print("[3/4] Evaluating 15-minute price horizon (native QH) ...")
    qh = D.load_price_qh()
    res_qh = M.evaluate_qh_price(qh)
    all_metrics["L3_price_15min"] = res_qh
    flat_rows += _flatten("L3_price", "15min", res_qh)
    print(f"      L3_price  15min   MAE={res_qh['model']['MAE']:8.2f} "
          f"| persist={res_qh['persistence']['MAE']:8.2f} "
          f"| skill={res_qh['skill_vs_persistence_MAE']:+.3f}")

    print("[4/4] Writing outputs ...")
    with open(C.OUTPUT_DIR / "metrics.json", "w") as f:
        json.dump(all_metrics, f, indent=2)
    summary = pd.DataFrame(flat_rows)
    summary.to_csv(C.OUTPUT_DIR / "metrics_summary.csv", index=False)

    print("\n=== Test-set metrics (held-out tail) ===")
    print(summary.to_string(index=False))
    print(f"\nOutputs in: {C.OUTPUT_DIR}")


if __name__ == "__main__":
    main()
