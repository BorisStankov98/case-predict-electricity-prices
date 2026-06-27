"""
Open the last-built HTML report in your default browser — without rebuilding it.

The pipeline runners (run_pipeline.py / run_no_scrape.py / model/run_all.py) all
regenerate the report before opening it. Use this when you just want to re-open
the report that is already on disk, or pull the latest one back from S3.

    python open_report.py            # open the local model/results/index.html
    python open_report.py --s3       # download the latest report from S3, then open it
    python open_report.py --local    # download from the local backend mirror, then open

With no flag it just opens the file already on disk. With --s3 / --local it fetches
data/results/index.html from that backend, overwrites the local copy, then opens it.

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
    if "--s3" in argv or "--local" in argv:
        if not download_report():
            return 1
    elif not REPORT.exists():
        print(f"(no report to open — {REPORT} not found)")
        print("  build it first (e.g. python model/run_all.py), or pull the "
              "uploaded one: python open_report.py --s3")
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
