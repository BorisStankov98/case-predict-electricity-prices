"""
Run the WHOLE forecasting pipeline end to end:

    scrape  →  clean-and-transform  →  features  →  model (+ HTML report)

Each stage is its own run_all.py; this script just chains them in order. The
transform / features / model stages run for EVERY built layer (Layer 1
consumption + Layer 2 supply), so a full run refreshes the whole report. The
stages hand off through the active storage backend — each reads the previous
stage's output and writes its own. By default that backend is S3 (the shared
bucket). Pass --local to use a local mirror (./local_store/) instead: a fully
local run still chains, because each stage reads what the previous one just
wrote locally. The default backend can also be set in .env
(STORAGE_BACKEND=s3|local); --local / --s3 override it for a single run.

Usage
-----
    python run_pipeline.py                           # use the .env / default backend (s3)
    python run_pipeline.py --from transform          # skip scraping; start at transform
    python run_pipeline.py --no-open                 # don't auto-open the report at the end
    python run_pipeline.py --local                   # local mirror under ./local_store/

Flags
-----
    --local         use the local backend (./local_store/) for read AND write
    --s3            force the S3 backend for this run
    --from STAGE    start at STAGE (scrape|transform|features|model); skip earlier stages
    --no-open       passed ONLY to the model stage (skip auto-opening the report)
    -h, --help      show this message and exit

Each stage runs as its own subprocess. A stage that ends non-zero (e.g. one
scraper missing an API key) is logged and the pipeline CONTINUES — the stage
run_all scripts are deliberately lenient — so check the summary at the end. The
exit code is the number of stages that ended non-zero (0 = all clean).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent

# Ordered pipeline stages: (name, run_all script, layers to run in order).
# An empty layer list = run the stage once with no layer arg (scrapers are
# shared across layers). The MODEL stage runs layer_2 BEFORE layer_1 on purpose:
# build_report.py (the last step of the layer_1 model run) aggregates every
# layer's figures from the backend, so layer_2 must be uploaded first for the
# final report to include fresh supply figures.
STAGES = [
    ("scrape",    "tools/scrapers/run_all.py",             []),
    ("transform", "tools/clean-and-transform/run_all.py",  ["layer_1", "layer_2"]),
    ("features",  "tools/features/run_all.py",             ["layer_1", "layer_2"]),
    ("model",     "model/run_all.py",                      ["layer_2", "layer_1"]),
]
STAGE_NAMES = [name for name, _, _ in STAGES]


def run_stage(label: str, script: str, args: list[str]) -> tuple[str, int, float]:
    """Run one stage's run_all.py as a subprocess; return (label, rc, seconds)."""
    banner = f"  STAGE: {label}  ({script} {' '.join(args)})  "
    print(f"\n\n{'#' * 78}\n#{banner:^76}#\n{'#' * 78}")
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(ROOT / script), *args])
    return label, proc.returncode, time.time() - t0


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

    # --from STAGE (default: scrape, i.e. run everything).
    start = "scrape"
    if "--from" in argv:
        i = argv.index("--from")
        if i + 1 >= len(argv) or argv[i + 1] not in STAGE_NAMES:
            sys.exit(f"--from needs one of: {', '.join(STAGE_NAMES)}")
        start = argv[i + 1]
    start_ix = STAGE_NAMES.index(start)

    sys.path.insert(0, str(ROOT / "tools"))
    from upload_s3 import describe_backend  # noqa: PLC0415

    selected = STAGES[start_ix:]
    print(f"\nPipeline: {' → '.join(n for n, _, _ in selected)}"
          f"  ·  layers: layer_1 + layer_2  ·  backend: {describe_backend()}")

    results = []
    interrupted = False
    for name, script, layers in selected:
        if not (ROOT / script).exists():
            print(f"\n⚠ skipping {name} — {script} not found")
            results.append((name, -1, 0.0))
            continue
        # Run the stage once per layer (or once with no layer for shared stages).
        for layer in (layers or [None]):
            label = f"{name}/{layer}" if layer else name
            args = ([layer] if layer else []) + list(override)
            # --no-open is a model-stage flag only; forwarding it to the scrapers
            # would break their positional-arg parsing. (Only the layer_1 model
            # run builds/opens the report; layer_2 produces no report.)
            if name == "model" and no_open:
                args.append("--no-open")
            try:
                results.append(run_stage(label, script, args))
            except KeyboardInterrupt:
                print("\nInterrupted — stopping the pipeline.")
                interrupted = True
                break
        if interrupted:
            break

    print(f"\n\n{'=' * 78}\nPIPELINE SUMMARY\n{'=' * 78}")
    failures = 0
    for name, rc, secs in results:
        status = "✓ ok" if rc == 0 else ("⚠ missing" if rc == -1 else f"✗ rc={rc}")
        if rc != 0:
            failures += 1
        print(f"  {status:<12} {name:<20} {secs:7.1f}s")
    mins = sum(s for _, _, s in results) / 60
    print(f"\n{len(results) - failures}/{len(results)} steps clean · total {mins:.1f} min")
    return failures


if __name__ == "__main__":
    sys.exit(main())