"""
Run every feature builder in one go (the feature stage).

Counterpart to tools/scrapers/run_all.py and tools/clean-and-transform/run_all.py.
Run this after the transform stage has populated data/processed/ in S3 with the
masters. Each builder still runs on its own too, e.g.
`python tools/features/feature_builder_1d.py --upload`.

Any arguments you pass are forwarded to each step, so:

    python tools/features/run_all.py            # run all, local only
    python tools/features/run_all.py --upload   # + push features to data/processed

Each step runs as its own subprocess, so one failure (missing master, a bad
row) is logged and skipped rather than aborting the whole batch. A summary is
printed at the end; the exit code is the number of failed steps (0 = all good).

Edit STEPS below to add/remove/reorder feature builders.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent

# Ordered list of feature builders to run (all live in this folder).
STEPS = [
    "feature_builder_1d.py",     # day-ahead (24h) features ← master_hourly_long_forecasted_weather
    "feature_builder_1w.py",     # week-ahead (168h) features ← master_1week_long
    "feature_builder_15min.py",  # 1h-ahead nowcast features ← same 1-day master (no separate transform)
]


def run_step(script: str, passthrough: list[str]) -> tuple[str, int, float]:
    """Run one script as a subprocess; return (name, returncode, seconds)."""
    print(f"\n{'=' * 70}\n▶ {script}  {' '.join(passthrough)}\n{'=' * 70}")
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(HERE / script), *passthrough],
                          cwd=HERE)
    return script, proc.returncode, time.time() - t0


def main() -> int:
    passthrough = sys.argv[1:]  # forward e.g. --upload to every step
    results = []
    for script in STEPS:
        if not (HERE / script).exists():
            print(f"\n⚠ skipping {script} — file not found")
            results.append((script, -1, 0.0))
            continue
        try:
            results.append(run_step(script, passthrough))
        except KeyboardInterrupt:
            print("\nInterrupted — stopping.")
            break

    print(f"\n\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    failures = 0
    for name, rc, secs in results:
        status = "✓ ok" if rc == 0 else ("⚠ missing" if rc == -1 else f"✗ rc={rc}")
        if rc != 0:
            failures += 1
        print(f"  {status:<12} {name:<42} {secs:6.1f}s")
    print(f"\n{len(results) - failures}/{len(results)} steps succeeded.")
    return failures


if __name__ == "__main__":
    sys.exit(main())