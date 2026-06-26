"""
Run the WHOLE forecasting pipeline end to end:

    scrape  →  clean-and-transform  →  features  →  model (+ HTML report)

Each stage is its own run_all.py; this script just chains them in order. The
stages hand off through S3 — every stage reads the previous stage's output from
data/raw/ or data/processed/ in the bucket — so you almost always want
--upload, otherwise a stage won't see what the one before it just produced.

Usage
-----
    python run_pipeline.py --upload                  # full pipeline via S3 (normal)
    python run_pipeline.py --upload --from transform # skip scraping; start at transform
    python run_pipeline.py --upload --no-open        # don't auto-open the report at the end
    python run_pipeline.py                           # local-only (warns: stages won't chain via S3)

Flags
-----
    --upload        forward to every stage (publish each stage's output to S3)
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

# Ordered pipeline stages: (name, run_all script relative to repo root).
STAGES = [
    ("scrape",    "tools/scrapers/run_all.py"),
    ("transform", "tools/clean-and-transform/run_all.py"),
    ("features",  "tools/features/run_all.py"),
    ("model",     "model/run_all.py"),
]
STAGE_NAMES = [name for name, _ in STAGES]


def run_stage(name: str, script: str, args: list[str]) -> tuple[str, int, float]:
    """Run one stage's run_all.py as a subprocess; return (name, rc, seconds)."""
    banner = f"  STAGE: {name}  ({script} {' '.join(args)})  "
    print(f"\n\n{'#' * 78}\n#{banner:^76}#\n{'#' * 78}")
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(ROOT / script), *args])
    return name, proc.returncode, time.time() - t0


def main() -> int:
    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0

    do_upload = "--upload" in argv
    no_open = "--no-open" in argv

    # --from STAGE (default: scrape, i.e. run everything).
    start = "scrape"
    if "--from" in argv:
        i = argv.index("--from")
        if i + 1 >= len(argv) or argv[i + 1] not in STAGE_NAMES:
            sys.exit(f"--from needs one of: {', '.join(STAGE_NAMES)}")
        start = argv[i + 1]
    start_ix = STAGE_NAMES.index(start)

    if not do_upload:
        print("⚠ WARNING: running WITHOUT --upload.\n"
              "  The stages hand off through S3, so each stage will read the LAST\n"
              "  uploaded output of the previous one (or fail if none exists) rather\n"
              "  than what this run just built locally. Add --upload for a real run.")

    selected = STAGES[start_ix:]
    print(f"\nPipeline: {' → '.join(n for n, _ in selected)}"
          f"{'  [--upload]' if do_upload else '  [local-only]'}")

    results = []
    for name, script in selected:
        if not (ROOT / script).exists():
            print(f"\n⚠ skipping {name} — {script} not found")
            results.append((name, -1, 0.0))
            continue
        args = ["--upload"] if do_upload else []
        # --no-open is a model-stage flag only; forwarding it to the scrapers
        # would break their positional-arg parsing.
        if name == "model" and no_open:
            args.append("--no-open")
        try:
            results.append(run_stage(name, script, args))
        except KeyboardInterrupt:
            print("\nInterrupted — stopping the pipeline.")
            break

    print(f"\n\n{'=' * 78}\nPIPELINE SUMMARY\n{'=' * 78}")
    failures = 0
    for name, rc, secs in results:
        status = "✓ ok" if rc == 0 else ("⚠ missing" if rc == -1 else f"✗ rc={rc}")
        if rc != 0:
            failures += 1
        print(f"  {status:<12} {name:<12} {secs:7.1f}s")
    mins = sum(s for _, _, s in results) / 60
    print(f"\n{len(results) - failures}/{len(results)} stages clean · total {mins:.1f} min")
    return failures


if __name__ == "__main__":
    sys.exit(main())