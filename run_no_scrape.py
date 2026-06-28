"""
Run the pipeline WITHOUT the scrapers — i.e. everything from the raw data
already in S3 onward, in order:

    clean-and-transform  →  features  →  model (+ HTML report)

Use this when the scrapers have already published data/raw/ to S3 and you only
want to rebuild the masters, features and models on top of it (the slow part is
scraping; this skips it). Every built layer is refreshed — Layer 1 (consumption)
and Layer 2 (supply) — so the report at the end reflects all layers.

The stages hand off through the active storage backend — each reads the
previous stage's output and writes its own. By default that backend is S3 (the
shared bucket). Pass --local to use a local mirror (./local_store/) instead: a
fully local run still chains, because each stage reads what the previous one
just wrote locally. The default backend can also be set in .env
(STORAGE_BACKEND=s3|local); --local / --s3 override it for a single run.

Usage
-----
    python run_no_scrape.py            # use the .env / default backend (s3)
    python run_no_scrape.py --local    # local mirror under ./local_store/
    python run_no_scrape.py --s3       # force the S3 backend for this run
    python run_no_scrape.py --no-open  # don't auto-open the report at the end

Flags
-----
    --local         use the local backend (./local_store/) for read AND write
    --s3            force the S3 backend for this run
    --no-open       don't auto-open the report at the end (headless/CI)
    -h, --help      show this message and exit

Each stage runs as its own subprocess. A stage that ends non-zero is logged and
the pipeline CONTINUES, so check the summary at the end. The exit code is the
number of stages that ended non-zero (0 = all clean).
"""

from __future__ import annotations

import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
# The model stage builds this self-contained report; we open it when done.
REPORT = ROOT / "model" / "results" / "index.html"

# Ordered stages (everything except the scrapers): (name, run_all script, layers).
# Each stage runs for every built layer. The MODEL stage runs layer_2 BEFORE
# layer_1 on purpose: build_report.py (last step of the layer_1 model run)
# aggregates every layer's figures from the backend, so layer_2 must be uploaded
# first for the final report to include fresh supply figures.
STAGES = [
    ("transform", "tools/clean-and-transform/run_all.py",  ["layer_1", "layer_2"]),
    ("features",  "tools/features/run_all.py",             ["layer_1", "layer_2"]),
    ("model",     "model/run_all.py",                      ["layer_2", "layer_1"]),
]


def run_stage(label: str, script: str, args: list[str]) -> tuple[str, int, float]:
    """Run one stage's run_all.py as a subprocess; return (label, rc, seconds)."""
    banner = f"  STAGE: {label}  ({script} {' '.join(args)})  "
    print(f"\n\n{'#' * 78}\n#{banner:^76}#\n{'#' * 78}")
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(ROOT / script), *args])
    return label, proc.returncode, time.time() - t0


def open_report() -> None:
    """Open the freshly built self-contained report in the default browser."""
    if not REPORT.exists():
        print(f"\n(no report to open — {REPORT} not found)")
        return
    print(f"\n🌐 opening report: {REPORT}")
    try:
        webbrowser.open(REPORT.resolve().as_uri())
    except Exception as e:  # noqa: BLE001
        print(f"  (could not open a browser: {e} — open it manually: {REPORT})")


def main() -> int:
    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0

    no_open = "--no-open" in argv
    # Explicit backend override to forward to every stage (else they inherit the
    # .env / default backend). --local wins over --s3 if both are given.
    override = (["--local"] if "--local" in argv
                else ["--s3"] if "--s3" in argv else [])

    sys.path.insert(0, str(ROOT / "tools"))
    from upload_s3 import describe_backend  # noqa: PLC0415

    print(f"\nPipeline (no scrape): {' → '.join(n for n, _, _ in STAGES)}"
          f"  ·  layers: layer_1 + layer_2  ·  backend: {describe_backend()}")

    results = []
    interrupted = False
    for name, script, layers in STAGES:
        if not (ROOT / script).exists():
            print(f"\n⚠ skipping {name} — {script} not found")
            results.append((name, -1, 0.0))
            continue
        # Run the stage once per layer (or once with no layer for shared stages).
        for layer in (layers or [None]):
            label = f"{name}/{layer}" if layer else name
            args = ([layer] if layer else []) + list(override)
            # The model substage would open the report itself; suppress that and
            # let this orchestrator open it once, at the very end (after summary).
            if name == "model":
                args.append("--no-open")
            try:
                results.append(run_stage(label, script, args))
            except KeyboardInterrupt:
                print("\nInterrupted — stopping the pipeline.")
                interrupted = True
                break
        if interrupted:
            break

    print(f"\n\n{'=' * 78}\nSUMMARY (no scrape)\n{'=' * 78}")
    failures = 0
    for name, rc, secs in results:
        status = "✓ ok" if rc == 0 else ("⚠ missing" if rc == -1 else f"✗ rc={rc}")
        if rc != 0:
            failures += 1
        print(f"  {status:<12} {name:<20} {secs:7.1f}s")
    mins = sum(s for _, _, s in results) / 60
    print(f"\n{len(results) - failures}/{len(results)} steps clean · total {mins:.1f} min")

    # Auto-open the report unless told not to (the model stage must have run).
    if not no_open and any(name.startswith("model") for name, _, _ in results):
        open_report()
    return failures


if __name__ == "__main__":
    sys.exit(main())