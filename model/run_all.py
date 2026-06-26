"""
Run every model builder in one go, then build the combined HTML report (the
model stage).

Counterpart to the scraper / transform / feature run_all scripts. Run this after
the feature stage has populated data/processed/ in S3. Each model builder still
runs on its own too, e.g.
    python model/model_builder_1d.py --upload
and the report can be regenerated on its own:
    python model/build_report.py --upload

Any arguments you pass are forwarded to each step, so:

    python model/run_all.py            # train + plot locally only
    python model/run_all.py --upload   # + upload PNGs to data/results/ and build/upload index.html
    python model/run_all.py --no-open  # don't pop the report open at the end (headless/CI)

When the run finishes and the report built OK, the self-contained
model/results/index.html is opened in your default browser (pass --no-open to
skip — e.g. on a headless server). --no-open is consumed here and not forwarded
to the steps.

The model builders write their PNGs to model/results/<horizon>/ and (with
--upload) push them to s3://…/data/results/<horizon>/. build_report.py then
pulls every result PNG back from S3 and bakes them into one self-contained
model/results/index.html (uploaded with --upload). Keep build_report.py LAST so
it sees the freshly uploaded figures.

Each step runs as its own subprocess, so one failure is logged and skipped
rather than aborting the whole batch. The exit code is the number of failed
steps (0 = all good).

Edit STEPS below to add/remove/reorder.
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

# Ordered list of scripts to run (all live in this folder).
# build_report.py MUST be last — it consumes the PNGs the builders upload.
STEPS = [
    "model_builder_1d.py",     # day-ahead (24h): 4 models vs ЕСО/naive → PNGs
    "model_builder_1w.py",     # week-ahead (168h): 4 models vs naive + intervals/selection → PNGs
    "model_builder_15min.py",  # 1h-ahead nowcast (+15min ramp): 4 models vs persistence → PNGs
    "build_report.py",         # gather all result PNGs from S3 → one self-contained index.html
]


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
    argv = sys.argv[1:]
    # --no-open: skip auto-opening the report (for headless/CI runs). Stripped
    # from the args forwarded to the steps (they don't know this flag).
    do_open = "--no-open" not in argv
    passthrough = [a for a in argv if a != "--no-open"]  # forward e.g. --upload
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
    report_ok = False
    for name, rc, secs in results:
        status = "✓ ok" if rc == 0 else ("⚠ missing" if rc == -1 else f"✗ rc={rc}")
        if rc != 0:
            failures += 1
        elif name == "build_report.py":
            report_ok = True
        print(f"  {status:<12} {name:<42} {secs:6.1f}s")
    print(f"\n{len(results) - failures}/{len(results)} steps succeeded.")

    # Open the report once the pipeline has finished and the report was built.
    if do_open and report_ok:
        open_report()
    return failures


if __name__ == "__main__":
    sys.exit(main())