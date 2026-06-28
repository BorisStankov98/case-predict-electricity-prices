"""
Run the clean/transform steps for one layer (the transform stage).

Counterpart to tools/scrapers/run_all.py. Run this after the scrapers have
populated S3 (or local data). Each transform still runs on its own too, e.g.
`python tools/clean-and-transform/transform_derive_available_capacity.py`.

Per-layer execution — pass the layer as the first positional argument:

    python tools/clean-and-transform/run_all.py            # Layer 1 (default): consumption masters
    python tools/clean-and-transform/run_all.py layer_1    # same, explicit
    python tools/clean-and-transform/run_all.py layer_2    # Layer 2: supply master

Any other arguments are forwarded to each step, so:

    python tools/clean-and-transform/run_all.py --local    # run all, local only (no upload)

Each step runs as its own subprocess, so one failure (missing input, a bad
row) is logged and skipped rather than aborting the whole batch. A summary is
printed at the end; the exit code is the number of failed steps (0 = all good).

Edit LAYERS below to add/remove/reorder transforms or layers.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent

# Per-layer transform scripts (all live flat in this folder). Order matters when
# one transform consumes another's output. Each reads raw from S3 (data/raw/) and
# uploads its master to data/processed/.
LAYERS = {
    "layer_1": [
        "transform_1_day_forecast_local_time.py",    # day-ahead (24h) master (forecast wx)
        "transform_1_week_forecast_local_time.py",   # week-ahead (168h) master (actual wx)
        "transform_derive_available_capacity.py",    # needs ENTSO-E data in data/raw
    ],
    "layer_2": [
        "transform_supply_master.py",                # supply master (generation + net imports + drivers)
    ],
    "layer_3": [],  # price side — not built yet
}
DEFAULT_LAYER = "layer_1"
# Note: transform_1_day_forecast_local_time.py supersedes the older
# timezone_convertor.py (same day-ahead master); the old script is kept on disk
# but no longer run from here.


def run_step(script: str, passthrough: list[str]) -> tuple[str, int, float]:
    """Run one script as a subprocess; return (name, returncode, seconds)."""
    print(f"\n{'=' * 70}\n▶ {script}  {' '.join(passthrough)}\n{'=' * 70}")
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(HERE / script), *passthrough],
                          cwd=HERE)
    return script, proc.returncode, time.time() - t0


def main() -> int:
    # First positional arg (not a --flag) selects the layer; default Layer 1.
    layer = DEFAULT_LAYER
    passthrough = []
    for a in sys.argv[1:]:
        if a.startswith("-"):
            passthrough.append(a)  # forward e.g. --local to every step
        else:
            layer = a              # positional → layer selector

    if layer not in LAYERS:
        sys.exit(f"unknown layer '{layer}' — choose one of: {', '.join(LAYERS)}")
    steps = LAYERS[layer]
    if not steps:
        print(f"transform stage · {layer}: no transforms yet — nothing to run.")
        return 0
    print(f"\ntransform stage · {layer}  ({len(steps)} step(s))")

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
    for name, rc, secs in results:
        status = "✓ ok" if rc == 0 else ("⚠ missing" if rc == -1 else f"✗ rc={rc}")
        if rc != 0:
            failures += 1
        print(f"  {status:<12} {name:<46} {secs:6.1f}s")
    print(f"\n{len(results) - failures}/{len(results)} steps succeeded.")
    return failures


if __name__ == "__main__":
    sys.exit(main())
