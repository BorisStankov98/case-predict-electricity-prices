"""
Regenerate the HTML report locally and open it — without uploading to S3.

For iterating on the report itself (wording, layout in model/build_report.py):
this rebuilds model/results/index.html from the result figures and opens it in
your browser, but does NOT push anything to S3. Tweak build_report.py, run this,
refresh — repeat. When you're happy, publish with `python upload_report.py`.

    python regen_report.py           # figures from S3 (default), write+open locally, no upload
    python regen_report.py --local   # figures from the local_store/ mirror instead of S3

By default the figures (PNGs) are read from S3 — so a fresh clone works without
running the whole pipeline — and only the local index.html is (re)written.

Exit code 0 if the report was built and opened, non-zero otherwise.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "model" / "build_report.py"


def main() -> int:
    # --no-upload: regenerate the local index.html only (figures still come from
    # the active backend). Forward any extra args (e.g. --local) to build_report.
    passthrough = [a for a in sys.argv[1:]]
    cmd = [sys.executable, str(BUILD), "--no-upload", *passthrough]
    print(f"▶ {' '.join(cmd)}")
    rc = subprocess.run(cmd, cwd=ROOT).returncode
    if rc != 0:
        print(f"(build_report.py exited {rc} — not opening)")
        return rc

    # Open the freshly built local report (no --s3/--local → just open the file).
    return subprocess.run([sys.executable, str(ROOT / "open_report.py")],
                          cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
