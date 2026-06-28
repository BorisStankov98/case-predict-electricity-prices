"""
Run the model stage for one layer, then (for Layer 1) build + open the report.

Counterpart to the scraper / transform / feature run_all scripts. Run this after
the feature stage has populated data/processed/ in S3. Each model builder still
runs on its own too, e.g.
    python model/layer_1/model_builder_1d.py
and the report can be regenerated on its own:
    python model/build_report.py

Per-layer execution — pass the layer as the first positional argument:

    python model/run_all.py             # Layer 1 (default): consumption/load
    python model/run_all.py layer_1     # same, explicit
    python model/run_all.py layer_2     # Layer 2: supply side (trains the supply models)

Layer 1 trains the three horizon builders (24h / 168h / 1h-ahead) and then
build_report.py bakes their PNGs into one self-contained model/results/index.html,
which is opened in your browser when done (pass --no-open to skip — e.g. on a
headless box). --no-open is consumed here and not forwarded to the steps.

Layer 2 (supply) trains the supply models (model/layer_2/model_builder_supply.py)
on the Layer 2 feature table and writes figures to data/results/supply/, which
build_report.py folds into the report's Layer 2 section. Its data engineering and
feature building live in the transform/feature stages
(tools/clean-and-transform/transform_supply_master.py and
tools/features/layer_2/feature_builder_supply.py), so the full Layer 2 chain is:
transform layer_2 → features layer_2 → model layer_2. The full-run orchestrators
(run_pipeline.py / run_no_scrape.py) run both layers; you can also run a single
layer here.

Any other arguments you pass are forwarded to each step, so:

    python model/run_all.py --local     # train + plot locally only (no upload)
    python model/run_all.py --no-open   # don't pop the report open at the end

Each step runs as its own subprocess, so one failure is logged and skipped
rather than aborting the whole batch. The exit code is the number of failed
steps (0 = all good).

Edit LAYERS below to add/remove/reorder steps or layers.
"""

from __future__ import annotations

import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).parent
# build_report.py writes the self-contained page here (also uploaded to S3).
REPORT = HERE / "results" / "index.html"

# Per-layer model steps (paths relative to model/). For a layer that produces
# report PNGs, keep build_report.py LAST — it consumes the PNGs the builders upload.
LAYERS = {
    # Layer 1 — consumption/load: 3 horizon builders + the combined HTML report.
    "layer_1": [
        "layer_1/model_builder_1d.py",     # day-ahead (24h): 4 models vs ЕСО/naive → PNGs
        "layer_1/model_builder_1w.py",     # week-ahead (168h): 4 models vs naive → PNGs
        "layer_1/model_builder_15min.py",  # 1h-ahead nowcast (+15min ramp) vs persistence → PNGs
        "build_report.py",                 # gather all result PNGs from S3 → one self-contained index.html
    ],
    # Layer 2 — supply side: trains the supply models on the Layer 2 feature
    # table and writes figures to data/results/supply/ (folded into the report).
    "layer_2": [
        "layer_2/model_builder_supply.py",
    ],
}
DEFAULT_LAYER = "layer_1"


def run_step(script: str, passthrough: list[str]) -> tuple[str, int, float]:
    """Run one script as a subprocess; return (name, returncode, seconds)."""
    print(f"\n{'=' * 70}\n▶ {script}  {' '.join(passthrough)}\n{'=' * 70}")
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(HERE / script), *passthrough],
                          cwd=HERE)
    return script, proc.returncode, time.time() - t0


def open_report() -> None:
    """Open the freshly built self-contained report in the default browser."""
    if not REPORT.exists():
        print(f"\n(no report to open — {REPORT} not found)")
        return
    print(f"\n🌐 opening report: {REPORT}")
    try:
        webbrowser.open(REPORT.resolve().as_uri())
    except Exception as e:
        print(f"  (could not open a browser: {e} — open it manually: {REPORT})")


def main() -> int:
    # First positional arg (not a --flag) selects the layer; default Layer 1.
    # --no-open is consumed here; every other flag is forwarded to the steps.
    layer = DEFAULT_LAYER
    do_open = "--no-open" not in sys.argv
    passthrough = []
    for a in sys.argv[1:]:
        if a == "--no-open":
            continue
        if a.startswith("-"):
            passthrough.append(a)  # forward e.g. --local / --s3
        else:
            layer = a              # positional → layer selector

    if layer not in LAYERS:
        sys.exit(f"unknown layer '{layer}' — choose one of: {', '.join(LAYERS)}")
    steps = LAYERS[layer]
    print(f"\nmodel stage · {layer}  ({len(steps)} step(s))")

    results = []
    for script in steps:
        if not (HERE / script).exists():
            print(f"\n⚠ skipping {script} — file not found")
            results.append((script, -1, 0.0))
            continue
        try:
            results.append(run_step(script, passthrough))
        except KeyboardInterrupt:
            print("\nInterrupted — stopping.")
            break

    print(f"\n\n{'=' * 70}\nSUMMARY · {layer}\n{'=' * 70}")
    failures = 0
    report_ok = False
    for name, rc, secs in results:
        status = "✓ ok" if rc == 0 else ("⚠ missing" if rc == -1 else f"✗ rc={rc}")
        if rc != 0:
            failures += 1
        elif name == "build_report.py":
            report_ok = True
        print(f"  {status:<12} {name:<42} {secs:6.1f}s")
    print(f"\n{len(results) - failures}/{len(results)} steps succeeded.")

    # Open the report once the stage has finished and the report was built
    # (Layer 1 only — other layers don't produce the combined index.html).
    if do_open and report_ok:
        open_report()
    return failures


if __name__ == "__main__":
    sys.exit(main())
