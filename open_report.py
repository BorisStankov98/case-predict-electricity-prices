"""
Open the last-built HTML report in your default browser — without rebuilding it.

The pipeline runners (run_pipeline.py / run_no_scrape.py / model/run_all.py) all
regenerate the report before opening it. Use this when you just want to re-open
the report that is already on disk, or pull the latest one back from S3.

    python open_report.py            # open the local report; if missing, pull it from S3
    python open_report.py --s3       # always download the latest report from S3, then open
    python open_report.py --local    # download from the local backend mirror, then open

With no flag it opens the file already on disk; if it isn't there (e.g. a fresh
clone), it automatically downloads data/results/index.html from the active backend
(S3 by default) first. --s3 / --local force a fresh download from that backend.

Exit code 0 if the report was opened, 1 otherwise (e.g. not built/uploaded yet).
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# build_report.py writes the self-contained page here (also uploaded to S3).
REPORT = ROOT / "model" / "results" / "index.html"
# The key the report is uploaded to on the active backend.
REPORT_KEY = "data/results/index.html"


def download_report() -> bool:
    """Pull data/results/index.html from the active backend into REPORT.

    The backend (s3/local) is chosen by upload_s3 from --s3/--local in argv,
    STORAGE_BACKEND, then the s3 default. Returns True on success.
    """
    sys.path.insert(0, str(ROOT / "tools"))  # make upload_s3 importable
    try:
        from upload_s3 import describe_backend, read_bytes  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print(f"(could not import upload_s3: {e})")
        return False
    try:
        data = read_bytes(REPORT_KEY)
    except Exception as e:  # noqa: BLE001
        print(f"(could not fetch {REPORT_KEY} from {describe_backend()}: {e})")
        return False
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_bytes(data)
    print(f"⬇  downloaded report from {describe_backend()} → {REPORT} "
          f"({len(data) / 1024:.0f} KB)")
    return True


def main() -> int:
    argv = sys.argv[1:]
    forced = "--s3" in argv or "--local" in argv
    # Force a fresh download when a backend flag is given; otherwise download only
    # if there's no local copy yet (e.g. a fresh clone that never ran the pipeline).
    if forced or not REPORT.exists():
        if not REPORT.exists() and not forced:
            print(f"(no local report at {REPORT} — pulling it from the active "
                  f"backend…)")
        if not download_report():
            if not forced:
                print("  build it first (e.g. python model/run_all.py), or force "
                      "a backend: python open_report.py --s3 / --local")
            return 1

    print(f"🌐 opening report: {REPORT}")
    try:
        webbrowser.open(REPORT.as_uri())
    except Exception as e:  # noqa: BLE001
        print(f"  (could not open a browser: {e} — open it manually: {REPORT})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
