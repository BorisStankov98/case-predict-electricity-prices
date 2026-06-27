"""
IBEX IDM 15-minute Prices & Volumes scraper — fast version.

Discovery:
  - The page calls https://ibex.bg/dev/ID24/data.php?but_search=YYYY-MM-DD
    and returns HTML containing TWO tables in the same response:
        <table class="idm-table">     → 60-minute products (rows: H-YYMMDD-HH)
        <table class="idm-qh-table">  → 15-minute products  (rows: QH-YYMMDD-QQ)
  - The "60'/15' PRODUCTS" toggle is purely client-side.
  - The endpoint sits behind a "SuperJS" anti-bot challenge that
    requires running JavaScript once to set a session cookie.

Strategy:
  1. Use Playwright once to load the page, pass the JS challenge,
     and extract the resulting cookies.
  2. Hand those cookies to a `requests` session and loop GET-ing
     /dev/ID24/data.php?but_search=<date> for each day in the window.
  3. Parse the idm-qh-table from each response with pandas.read_html
     and concatenate.

Requirements:
    pip install playwright pandas requests beautifulsoup4 lxml html5lib
    playwright install chromium

Usage:
    python scrape_ibex_idm_15min.py
    python scrape_ibex_idm_15min.py 2026-02-09 2026-05-08
"""

from __future__ import annotations

import io
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
from playwright.sync_api import sync_playwright

PAGE_URL = "https://ibex.bg/markets/idm/idm-prices-volumes-with-qh/"
DATA_URL = "https://ibex.bg/dev/ID24/data.php"


# ---------- helpers ----------

def daterange(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def get_session_with_superjs_cookie() -> requests.Session:
    """Open the page once with a headless browser to clear the SuperJS
    anti-bot challenge, then copy its cookies into a requests session."""
    print("Bootstrapping session (passing SuperJS challenge)…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(PAGE_URL, wait_until="networkidle", timeout=60_000)
        # Give the challenge a moment to complete and set cookies
        page.wait_for_timeout(2_000)
        cookies = ctx.cookies()
        ua = page.evaluate("() => navigator.userAgent")
        browser.close()

    s = requests.Session()
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
    s.headers.update({
        "User-Agent": ua,
        "Referer": PAGE_URL,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
    })
    print("  \u2713 session ready")
    return s


def fetch_day(session: requests.Session, day: date, retries: int = 3) -> str | None:
    """GET the data page for one delivery date and return the HTML body.
    Returns None if the cookie has expired (caller should refresh)."""
    params = {"but_search": day.isoformat()}
    for attempt in range(1, retries + 1):
        try:
            r = session.get(DATA_URL, params=params, timeout=30)
            if r.status_code == 200 and "idm-qh-table" in r.text:
                return r.text
            if r.status_code == 200 and "SuperJS" in r.text:
                return None  # signal: need fresh cookie
            print(f"  ! HTTP {r.status_code} on {day} (attempt {attempt})")
        except requests.RequestException as e:
            print(f"  ! network error on {day} (attempt {attempt}): {e}")
        time.sleep(2 * attempt)
    return None


def parse_qh_table(html: str, delivery_date: date) -> pd.DataFrame | None:
    """Pull the 15-minute (QH) table out of the page HTML."""
    # IBEX prints numbers in European format ("132,65" = 132.65), so tell
    # pandas the comma is the decimal mark and the dot the thousands group.
    # Without this, read_html's default thousands="," turns 132,65 into 13265.
    try:
        tables = pd.read_html(
            io.StringIO(html),
            attrs={"class": "idm-qh-table"},
            decimal=",",
            thousands=".",
        )
    except ValueError:
        # Fallback: any table whose first column has rows starting with "QH-"
        try:
            all_tables = pd.read_html(io.StringIO(html), decimal=",", thousands=".")
        except ValueError:
            return None
        tables = [
            t for t in all_tables
            if t.shape[0] > 0
            and t.iloc[:, 0].astype(str).str.startswith("QH-").any()
        ]
        if not tables:
            return None

    df = None
    for t in tables:
        if t.iloc[:, 0].astype(str).str.startswith("QH-").any():
            df = t
            break
    if df is None or df.empty:
        return None

    df.insert(0, "delivery_date", delivery_date.isoformat())
    return df


# ---------- main ----------

def scrape(start: date, end: date) -> pd.DataFrame:
    session = get_session_with_superjs_cookie()
    frames: list[pd.DataFrame] = []
    n_days = (end - start).days + 1
    print(f"Fetching {n_days} days: {start} \u2192 {end}")

    for i, day in enumerate(daterange(start, end), 1):
        html = fetch_day(session, day)
        if html is None:
            print("  \u2192 cookie may have expired, refreshing\u2026")
            session = get_session_with_superjs_cookie()
            html = fetch_day(session, day)
        if html is None:
            print(f"[{i}/{n_days}] {day}  \u2717 no data")
            continue

        df = parse_qh_table(html, day)
        if df is None or df.empty:
            print(f"[{i}/{n_days}] {day}  - empty")
            continue

        print(f"[{i}/{n_days}] {day}  \u2713 {len(df)} rows")
        frames.append(df)
        time.sleep(0.4)  # be polite

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def main() -> int:
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=89)  # 90 days inclusive

    do_upload = True  # always persist; backend (s3/local) chosen in upload_s3
    args = [a for a in sys.argv[1:] if a not in ("--local", "--s3")]

    if len(args) == 2:
        start = datetime.strptime(args[0], "%Y-%m-%d").date()
        end = datetime.strptime(args[1], "%Y-%m-%d").date()

    df = scrape(start, end)

    if df.empty:
        print("No data collected.")
        return 1

    out = Path(__file__).parent / f"ibex_idm_15min_{start}_{end}.csv"
    df.to_csv(out, index=False)
    print(f"\n\u2713 Wrote {len(df):,} rows \u00d7 {df.shape[1]} cols \u2192 {out}")
    print(f"  columns: {list(df.columns)}")

    if do_upload:
        import pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from upload_s3 import upload
        upload(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
