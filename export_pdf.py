"""
export_pdf.py — render the HTML report to a PDF with headless Chromium (Playwright).

By default the whole report is printed as ONE long page (no pagination). The
report is very tall, and Chromium caps a single PDF page at ~200 inches, so the
content is auto-scaled down just enough to fit on one page. Pass --paged for a
normal A4 multi-page PDF at full size instead.

The dark theme is preserved (backgrounds on); the sticky nav is hidden via the
@media print rules in build_report.py.

    python export_pdf.py                 # one long page → solution/report.pdf
    python export_pdf.py --paged         # A4 multi-page instead
    python export_pdf.py out.pdf         # custom output path (either mode)

Needs Playwright + Chromium (already used by the IBEX scraper):
    pip install -r requirements.txt
    playwright install chromium
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "model" / "results" / "index.html"
OUT_DEFAULT = ROOT / "solution" / "report.pdf"   # root-level solution/ folder
MAX_PAGE_IN = 199.0          # Chromium's per-page hard cap is ~200 inches
CSS_PX_PER_IN = 96.0


def main() -> int:
    args = sys.argv[1:]
    paged = "--paged" in args
    rest = [a for a in args if a != "--paged"]
    out = Path(rest[0]).resolve() if rest else OUT_DEFAULT
    out.parent.mkdir(parents=True, exist_ok=True)

    if not REPORT.exists():
        print(f"(no report at {REPORT} — build it first, e.g. python regen_report.py)")
        return 1
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print(f"(Playwright not available: {e}\n"
              f" install it: pip install playwright && playwright install chromium)")
        return 1

    mode = "A4 multi-page" if paged else "one long page"
    print(f"🖨  rendering {REPORT.name} → {out}  ({mode}) …")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1100, "height": 1400})
            page.goto(REPORT.as_uri(), wait_until="networkidle")
            page.emulate_media(media="print")     # apply @media print (hide nav, full width)

            if paged:
                page.pdf(path=str(out), format="A4", print_background=True,
                         margin={"top": "12mm", "bottom": "12mm",
                                 "left": "10mm", "right": "10mm"})
            else:
                dims = page.evaluate(
                    "() => ({ w: document.documentElement.scrollWidth,"
                    "         h: document.documentElement.scrollHeight })")
                w, h = dims["w"], dims["h"]
                # Scale down so the full height fits in one page under the cap.
                scale = min(1.0, MAX_PAGE_IN / (h / CSS_PX_PER_IN))
                page.pdf(path=str(out), print_background=True, scale=scale,
                         width=f"{w * scale:.0f}px", height=f"{h * scale:.0f}px",
                         margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
                if scale < 1.0:
                    print(f"   (content {h / CSS_PX_PER_IN:.0f}in tall → scaled to "
                          f"{scale * 100:.0f}% to fit one {h * scale / CSS_PX_PER_IN:.0f}in page)")
            browser.close()
    except Exception as e:  # noqa: BLE001
        print(f"(failed to render PDF: {e}\n"
              f" if Chromium is missing: playwright install chromium)")
        return 1

    print(f"✅ {out}  ({out.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
