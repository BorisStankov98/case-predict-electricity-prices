"""
Upload the local HTML report to the active backend — without rebuilding it.

The pipeline runners (and build_report.py) upload the report as part of building
it. Use this when you already have a good model/results/index.html on disk and
just need to push it up (e.g. the copy in S3 is stale).

    python upload_report.py          # upload to the active backend (S3 by default)
    python upload_report.py --s3     # force upload to S3
    python upload_report.py --local  # force copy into the local_store/ mirror

It uploads model/results/index.html to data/results/index.html — the same key
build_report.py uses — so `python open_report.py --s3` then fetches this copy.

Exit code 0 if the report was uploaded, 1 otherwise (e.g. no local report yet).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# build_report.py writes the self-contained page here.
REPORT = ROOT / "model" / "results" / "index.html"
# Same prefix build_report.py uploads to → data/results/index.html.
REPORT_PREFIX = "data/results"


def main() -> int:
    if not REPORT.exists():
        print(f"(no report to upload — {REPORT} not found)")
        print("  build it first, e.g.: python model/run_all.py --local")
        return 1

    sys.path.insert(0, str(ROOT / "tools"))  # make upload_s3 importable
    try:
        from upload_s3 import describe_backend, upload  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print(f"(could not import upload_s3: {e})")
        return 1

    print(f"⬆  uploading {REPORT.name} ({REPORT.stat().st_size / 1024:.0f} KB) "
          f"→ {describe_backend()} :: {REPORT_PREFIX}/{REPORT.name}")
    ok = upload(REPORT, prefix=REPORT_PREFIX)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
