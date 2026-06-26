"""
build_report.py — collect every model-result PNG from S3 and bake them into ONE
self-contained HTML page (images base64-embedded, so there are no broken links
and the file opens anywhere offline).

Reads:   s3://<bucket>/data/results/**/*.png   (uploaded by the model builders)
Writes:  ./results/index.html                  (local, always)
Uploads: s3://<bucket>/data/results/index.html (only with --upload)

The page is grouped by horizon (1d, 1week), newest build wins because the model
builders overwrite their PNGs in place.

Usage:
    python build_report.py            # build index.html locally from S3 PNGs
    python build_report.py --upload   # + upload the page to data/results/
"""
import base64
import html
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the shared tools/ dir importable (for upload_s3) from model/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from upload_s3 import list_keys, read_bytes, upload  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RESULTS_PREFIX = "data/results/"
OUT_NAME = "index.html"

# Friendly section titles per horizon folder, and the order they appear.
SECTIONS = [
    ("15min", "1h-ahead nowcast (→ 15-min ramp) — 4 models vs persistence"),
    ("1d", "Day-ahead (24h) — 4 models vs ЕСО / naive"),
    ("1week", "Week-ahead (168h) — 4 models vs naive"),
]
# Preferred ordering of figures within a section (prefix match); the rest follow
# alphabetically.
FIG_ORDER = ["pipeline_metrics", "pipeline_significant", "pipeline_selection",
             "pipeline_diagnostics", "pipeline_intervals", "pipeline_learning_curve",
             "pipeline_final", "pipeline_15min_ramp", "pipeline_corr"]


def horizon_of(key: str) -> str | None:
    """Map an S3 key like data/results/1week/foo.png → '1week'."""
    rest = key[len(RESULTS_PREFIX):]
    parts = rest.split("/")
    return parts[0] if len(parts) > 1 else None


def fig_sort_key(name: str):
    for i, pref in enumerate(FIG_ORDER):
        if name.startswith(pref):
            return (i, name)
    return (len(FIG_ORDER), name)


def main() -> int:
    do_upload = "--upload" in sys.argv

    keys = [k for k in list_keys(RESULTS_PREFIX, ".png")]
    by_horizon: dict[str, list[str]] = {}
    for k in keys:
        hz = horizon_of(k)
        if hz:
            by_horizon.setdefault(hz, []).append(k)
    print(f"found {len(keys)} PNG(s) across {len(by_horizon)} horizon(s)")

    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [_HEAD.format(built=built)]

    # Known sections first, then any extra horizons that showed up.
    ordered = [h for h, _ in SECTIONS] + sorted(set(by_horizon) - {h for h, _ in SECTIONS})
    titles = dict(SECTIONS)

    any_figs = False
    for hz in ordered:
        figs = by_horizon.get(hz)
        if not figs:
            continue
        any_figs = True
        figs = sorted(figs, key=lambda k: fig_sort_key(k.rsplit("/", 1)[-1]))
        parts.append(f'<section><h2>{html.escape(titles.get(hz, hz))}</h2>')
        for k in figs:
            name = k.rsplit("/", 1)[-1]
            b64 = base64.b64encode(read_bytes(k)).decode("ascii")
            parts.append(
                f'<figure><figcaption>{html.escape(name)}</figcaption>'
                f'<img alt="{html.escape(name)}" src="data:image/png;base64,{b64}"></figure>')
            print(f"  embedded {k}")
        parts.append("</section>")

    if not any_figs:
        parts.append('<section><p class="empty">No result PNGs found in S3 yet — '
                     'run the model builders with <code>--upload</code> first.</p></section>')

    parts.append(_FOOT)
    page = "\n".join(parts)

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / OUT_NAME
    out_path.write_text(page, encoding="utf-8")
    print(f"\n✅ wrote {out_path} ({len(page)/1024:.0f} KB)")

    if do_upload:
        upload(out_path, prefix="data/results")
    return 0


_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Electricity load forecast — results</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
         background: #0f172a; color: #e2e8f0; }}
  header {{ padding: 24px 32px; background: #1e293b; border-bottom: 1px solid #334155; }}
  header h1 {{ margin: 0 0 4px; font-size: 20px; }}
  header p {{ margin: 0; color: #94a3b8; font-size: 13px; }}
  nav {{ padding: 12px 32px; background: #162033; font-size: 13px; }}
  nav a {{ color: #60a5fa; margin-right: 16px; text-decoration: none; }}
  main {{ padding: 24px 32px 64px; max-width: 1200px; margin: 0 auto; }}
  section {{ margin-bottom: 48px; }}
  section h2 {{ font-size: 16px; border-left: 4px solid #2563eb; padding-left: 10px; }}
  figure {{ margin: 0 0 28px; background: #fff; border-radius: 8px; overflow: hidden;
           box-shadow: 0 1px 4px rgba(0,0,0,.4); }}
  figcaption {{ font-family: ui-monospace, monospace; font-size: 12px; color: #334155;
               padding: 8px 12px; background: #f1f5f9; border-bottom: 1px solid #e2e8f0; }}
  img {{ display: block; width: 100%; height: auto; }}
  .empty {{ color: #94a3b8; }}
  code {{ background: #1e293b; padding: 1px 5px; border-radius: 4px; }}
</style>
</head>
<body>
<header>
  <h1>Electricity load forecast — model results</h1>
  <p>Built {built} · figures pulled from s3://…/data/results/ (self-contained, images embedded)</p>
</header>
<main>"""

_FOOT = """</main>
</body>
</html>"""


if __name__ == "__main__":
    sys.exit(main())