# Bulgarian Electricity Data Pipeline

> Our solution for the Bulgarian electricity forecasting case: the data
> pipeline that scrapes, stores, cleans, and joins every input the
> forecasting models need. For the conceptual background and market
> terminology, see [`docs/`](docs/) (start with
> [docs/concepts.md](docs/concepts.md) and [docs/scope.md](docs/scope.md)).

This repo is organised around a simple idea: **S3 is the single source of
truth.** Scrapers pull raw data from the public sources and push it to
S3 (`data/raw/`). Transforms read those raw files back from S3, build a
canonical hourly modelling dataset, and push it to S3
(`data/processed/`). No local file is authoritative — anyone with the
credentials can reproduce the dataset from scratch.

---

## Repository structure

```
case-predict-electricity-prices/
├── README.md                       ← you are here: the pipeline
├── requirements.txt                ← Python dependencies
├── LICENSE                         ← MIT
│
├── docs/                           ← case documentation (concepts, data, scope…)
│
├── tools/                          ← the pipeline
│   ├── upload_s3.py                ← shared S3 helper (upload + read-back)
│   │
│   ├── scrapers/                   ← STAGE 1: raw sources → S3 data/raw/
│   │   ├── run_all.py              ← run every scraper in sequence
│   │   ├── scrape_entsoe_bulgaria.py       ← ENTSO-E: prices, load, generation,
│   │   │                                     cross-border, outages, capacity
│   │   ├── scrape_weather_bulgaria.py      ← Open-Meteo weather, 5 cities + avg
│   │   ├── scrape_forecast.py              ← tomorrow's live weather forecast
│   │   ├── scrape_1day_ahead_forecast.py   ← fixed 24h-lead forecast archive
│   │   ├── scrape_historical_forecast.py   ← best-available forecast archive
│   │   ├── scrape_ibex_idm_15min.py        ← IBEX intraday 15-min prices/volumes
│   │   └── scrape_days_off_bulgaria.py     ← weekends + public holidays calendar
│   │
│   └── clean-and-transform/        ← STAGE 2: S3 data/raw/ → S3 data/processed/
│       ├── run_all.py              ← run every transform in sequence
│       ├── timezone_convertor.py             ← builds the hourly master dataset
│       └── transform_derive_available_capacity.py  ← available capacity per fuel
│
└── data/                           ← provided seed data (snapshots, go stale)
```

---

## The data pipeline

```
  Public sources              STAGE 1: scrape           STAGE 2: transform
  ──────────────              ───────────────           ──────────────────
  ENTSO-E  ┐
  Open-Meteo├─►  tools/scrapers/*  ──►  s3://…/data/raw/  ──►  tools/clean-and-transform/*  ──►  s3://…/data/processed/
  IBEX     │         (--upload)                                      (--upload)                   master_hourly_*.csv
  holidays ┘                                                                                       (model-ready)
```

Both stages follow the same conventions:

- **`--upload`** — every script writes its output locally; pass `--upload`
  to also push it to S3. Without it, the script is a pure local run.
- **`run_all.py`** — an optional orchestrator per stage. It runs each
  script as its own subprocess, so one failure (missing API key, network
  blip, missing input) is logged and skipped rather than aborting the
  batch. It forwards any arguments to every step (so
  `run_all.py --upload` uploads everything) and exits with the number of
  failed steps.

Each script also runs perfectly well on its own — `run_all.py` is just a
convenience wrapper.

---

## Quickstart

```bash
# 1. Install dependencies (from the project root)
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium          # only needed for the IBEX scraper

# 2. Configure S3 + AWS credentials (see "Configuration" below)
export AWS_ACCESS_KEY_ID=...   AWS_SECRET_ACCESS_KEY=...   AWS_DEFAULT_REGION=eu-central-1
export S3_BUCKET=your-bucket-name

# 3. Set the ENTSO-E token (free; takes a few working days to obtain)
export ENTSOE_API_KEY=your-token-here

# 4. STAGE 1 — scrape everything to S3 data/raw/
python tools/scrapers/run_all.py --upload

# 5. STAGE 2 — build the canonical dataset to S3 data/processed/
python tools/clean-and-transform/run_all.py --upload
```

Drop `--upload` at any step to run locally without touching S3.

---

## Stage 1 — Scrapers (`tools/scrapers/`)

Each scraper takes an optional `[START END]` window (dates `YYYY-MM-DD`)
and an optional `--upload` flag. Output lands in a folder/file next to the
script, and (with `--upload`) under `data/raw/` in S3.

| Script | Source | What it collects |
| --- | --- | --- |
| **`scrape_entsoe_bulgaria.py`** | ENTSO-E (needs `ENTSOE_API_KEY`) | The big one. Day-ahead + intraday (IDA1/2/3) prices, actual/forecast load, generation per type, installed capacity, generation & wind/solar forecasts, imbalance, cross-border flows/schedules/transfer-capacity with 5 neighbours, and unit outages. Long ranges are split into yearly chunks. Writes `entsoe_bg/*.csv` + `_summary.json`. |
| **`scrape_weather_bulgaria.py`** | Open-Meteo (no key) | Hourly weather for 5 cities + a country average. Stitches ERA5 actuals with a forecast tail to cover ERA5's ~5-day lag, plus a separate leakage-safe forecast series. |
| **`scrape_forecast.py`** | Open-Meteo | **Tomorrow's** hourly forecast (9 weather vars), UTC, country centroid. |
| **`scrape_1day_ahead_forecast.py`** | Open-Meteo Previous-Runs API | Fixed **24h-lead** forecast archive (what was predicted exactly a day before each timestamp) from 2024-02-17. The leakage-safe weather feature the master dataset uses. |
| **`scrape_historical_forecast.py`** | Open-Meteo Historical-Forecast API | Best-available archived forecasts (the runs models actually issued) from 2022-01-01. |
| **`scrape_ibex_idm_15min.py`** | ibex.bg (Playwright + requests) | IBEX intraday 15-min (QH) prices & volumes. Clears a JS anti-bot challenge with headless Chromium once, then reuses the cookie. Limited to a rolling ~3 months by IBEX. |
| **`scrape_days_off_bulgaria.py`** | `holidays` library | One row per day-off (weekends + public holidays incl. Orthodox Easter & substitute days). |

---

## Stage 2 — Clean & transform (`tools/clean-and-transform/`)

These read raw inputs **back from S3** (`data/raw/`), build canonical
outputs, and (with `--upload`) push them to `data/processed/`. Order
matters — see `run_all.py`'s `STEPS`.

| Script | What it builds |
| --- | --- |
| **`timezone_convertor.py`** | The **master dataset**. Joins load actual + ESO load forecast (ENTSO-E), the 1-day-ahead forecasted weather, and the days-off calendar; canonicalises everything in UTC; builds an hourly grid from 2024-02-17; flags `is_day_off` by **local** date; trims to where real load exists; converts to local BG time (`Europe/Sofia`). Output: `master_hourly_long_forecasted_weather.csv`. |
| **`transform_derive_available_capacity.py`** | Hourly **available** generation capacity per fuel type — since ENTSO-E only publishes nameplate capacity yearly. `available = nameplate(year) − Σ outage MW lost`, joined 1:1 onto the generation timeline. |

### The master dataset

`data/processed/master_hourly_long_forecasted_weather.csv` is the
model-ready table. One row per hour in local BG time, columns:

- `load_actual_mw`, `load_forecast_mw` (ENTSO-E / ESO)
- forecasted weather for hour *T*: `temp_c`, `wind10_ms`, `wind100_ms`,
  `wind_dir_100m_deg`, `ghi_wm2`, `dni_wm2`, `cloud_pct`, `precip_mm`,
  `rh_pct`
- `is_day_off`

The weather is a **day-ahead forecast** (not actuals, not lagged), so
using it as a feature does not leak future information.

---

## Configuration

`tools/upload_s3.py` handles all S3 I/O via the standard AWS credential
chain (env vars, `~/.aws/credentials`, or an IAM role) — no browser flow,
nothing to refresh. Per-device setup is just: clone, install, set these
env vars.

| Variable | Purpose |
| --- | --- |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS credentials (required) |
| `AWS_DEFAULT_REGION` | e.g. `eu-central-1` (required for AWS) |
| `S3_BUCKET` | Target bucket (required for `--upload` and the transforms) |
| `S3_PREFIX` | Key prefix; default `data/raw` |
| `S3_ENDPOINT_URL` | Only for R2/B2/MinIO; omit for AWS S3 |
| `S3_DELETE_LOCAL` | `1`/`true`/`yes` → delete the local copy after a successful upload |
| `ENTSOE_API_KEY` | ENTSO-E token (only the ENTSO-E scraper needs it) |

If boto3 isn't installed or `S3_BUCKET` isn't set, `--upload` is a
graceful no-op (warns, doesn't crash) so scrapers still work offline.

---

## What the pipeline feeds

The dataset above feeds the three forecasting layers the case asks for —
**consumption** (Layer 1), **supply** (Layer 2), and **price** (Layer 3),
each at 15-minute, 24-hour, and 1-week horizons. The conceptual framing
and required deliverables live in the docs:

- **[docs/concepts.md](docs/concepts.md)** — market concepts and terminology.
- **[docs/data.md](docs/data.md)** — data sources, access, lags, gotchas.
- **[docs/practices.md](docs/practices.md)** — evaluation, reproducibility,
  avoiding look-ahead leakage.
- **[docs/scope.md](docs/scope.md)** — required deliverables and optional directions.

---

## Licence

MIT ([LICENSE](LICENSE)).