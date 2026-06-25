"""
Run every scraper (and the derive transform) in one go.

This is an OPTIONAL convenience wrapper — each scraper still runs perfectly
well on its own (e.g. `python tools/scrape_weather_bulgaria.py --upload`).
This just runs them all in sequence.

Any arguments you pass are forwarded to each script, so:

    python tools/run_all.py              # run all, local only
    python tools/run_all.py --upload     # run all, push each output to S3

Each step runs as its own subprocess, so one failure (a missing API key,
Playwright not installed, a network blip) is logged and skipped rather than
aborting the whole batch. A summary is printed at the end and the exit code
is the number of failed steps (0 = all good).

The transform runs last because it consumes the ENTSO-E scraper's output.
Edit STEPS below to add/remove/reorder scripts.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).parent

# Ordered list of scripts to run. The ENTSO-E scraper must precede the
# transform, which reads its output. Comment out any you don't want.
STEPS = [
    "scrape_entsoe_bulgaria.py",          # needs ENTSOE_API_KEY
    "scrape_weather_bulgaria.py",
    "scrape_forecast.py",                 # tomorrow's live forecast
    "scrape_1day_ahead_forecast.py",      # fixed 24h-lead forecast archive
    "scrape_historical_forecast.py",      # best-available forecast archive
    "scrape_ibex_idm_15min.py",           # needs Playwright + chromium
    "scrape_days_off_bulgaria.py",
    "transform_derive_available_capacity.py",  # last: consumes ENTSO-E output
]


def run_step(script: str, passthrough: list[str]) -> tuple[str, int, float]:
    """Run one script as a subprocess; return (name, returncode, seconds)."""
    print(f"\n{'=' * 70}\n▶ {script}  {' '.join(passthrough)}\n{'=' * 70}")
    t0 = time.time()
    # cwd=TOOLS so the forecast scrapers' relative-path CSVs land in tools/
    # alongside everyone else's output.
    proc = subprocess.run([sys.executable, str(TOOLS / script), *passthrough],
                          cwd=TOOLS)
    return script, proc.returncode, time.time() - t0


def main() -> int:
    passthrough = sys.argv[1:]  # forward e.g. --upload to every script
    results = []
    for script in STEPS:
        if not (TOOLS / script).exists():
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
