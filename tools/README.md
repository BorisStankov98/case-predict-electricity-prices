# Provided tools

Three working scrapers, one per primary data source. Each is a single
self-contained Python script with a docstring at the top explaining
its behaviour in detail. Run them from this directory (output folders
are created next to the script).

## Common setup

Python 3.10+ recommended. A clean virtual environment avoids the
dependency conflicts that plague long-lived conda installations:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install requests beautifulsoup4 lxml html5lib pandas entsoe-py playwright
playwright install chromium      # only needed for the IBEX scraper
```

## 1. `scrape_ibex_idm_15min.py` — IBEX continuous intraday, 15-minute

Source: <https://ibex.bg/markets/idm/idm-prices-volumes-with-qh/>

```bash
python scrape_ibex_idm_15min.py                       # last ~90 days
python scrape_ibex_idm_15min.py 2026-05-01 2026-05-31 # explicit window
```

Output: one CSV with one row per quarter-hour per delivery date —
weighted-average / max / min / last price (EUR/MWh) and volume (MW).

Notes:

- IBEX's backend limits this data to a **rolling 3 months**. Older
  dates return "No record found" — that limit is theirs, not the
  scraper's.
- The site is protected by a JavaScript anti-bot challenge. The script
  opens the page once in a headless browser to obtain the cookie, then
  switches to plain HTTP requests. If the cookie expires mid-run, it
  refreshes automatically.
- Prices use a comma as the decimal separator on the site; check the
  dtypes after loading the CSV and convert if necessary.

## 2. `scrape_entsoe_bulgaria.py` — ENTSO-E Transparency Platform

Source: <https://transparency.entsoe.eu>

Requires a free API token:

1. Register at the URL above.
2. Email `transparency@entsoe.eu`, subject `Restful API access`, body
   `I want to request access to the Restful API`.
3. The token arrives within a few working days and then also appears
   in your account settings.

```bash
export ENTSOE_API_KEY="your-token-here"
python scrape_entsoe_bulgaria.py                       # 2022-09-30 → today
python scrape_entsoe_bulgaria.py 2025-01-01 2025-12-31 # explicit window
```

Output: `entsoe_bg/` with one CSV per dataset plus `_summary.json`
listing row counts, date ranges, and which datasets returned no data.

Notes:

- A full multi-year run makes hundreds of API calls (5 neighbours ×
  several cross-border series × yearly chunks) and takes 10–20
  minutes. Test with a one-month window first.
- Several datasets legitimately return "no data" for Bulgaria:
  imbalance prices/volumes, unavailability of *production* units
  (generation-unit outages ARE published), IDA intraday auction
  prices, and some week-/year-ahead transfer capacities on non-EU
  borders. The summary file tells you exactly which.
- Day-ahead prices switch from hourly to 15-minute resolution at
  2025-10-01 (the SDAC 15-minute go-live). Handle both resolutions
  downstream.

## 3. `scrape_weather_bulgaria.py` — Open-Meteo hourly weather

Source: <https://open-meteo.com> (free, no token)

```bash
python scrape_weather_bulgaria.py                       # 2026-02-01 → today
python scrape_weather_bulgaria.py 2025-06-01 2026-06-01 # explicit window
```

Output: `weather_bg/` with one CSV per city (Sofia, Plovdiv, Varna,
Burgas, Ruse), one country-average CSV, and `_summary.json`.

Notes:

- The ERA5 reanalysis archive lags real time by ~5 days. The script
  fills the gap with Open-Meteo's historical-forecast archive and
  marks each row's origin in a `source` column.
- Wind direction in the country-average file is computed as a proper
  speed-weighted vector mean (no 359°/1° wrap-around artefacts).
- The country average is unweighted across the five cities. For
  load modelling you may want to re-weight by population; for
  renewables, by installed capacity location. That's your call.

## Extending the tools

These scripts are deliberately simple: sequential requests, CSV
output, no databases, no schedulers. Obvious extensions if your team
needs them — incremental updates instead of full re-downloads,
neighbour-country ENTSO-E data (change the country code), more weather
locations or variables (edit the constants at the top), output to
Parquet for faster loading.
