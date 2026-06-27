# Bulgarian Electricity Data Pipeline

> Our solution for the Bulgarian electricity forecasting case: the data
> pipeline that scrapes, stores, cleans, and joins every input the
> forecasting models need. For the conceptual background and market
> terminology, see [`docs/`](docs/) (start with
> [docs/concepts.md](docs/concepts.md) and [docs/scope.md](docs/scope.md)).

This repo is organised around a simple idea: **S3 is the single source of
truth.** Scrapers pull raw data from the public sources and push it to
S3 (`data/raw/`). Transforms read those raw files back from S3, build the
canonical hourly **master** datasets, and push them to S3
(`data/processed/`). Feature builders turn the masters into model-ready
**feature** tables (`data/processed/`), and the model stage trains the
forecasters — writing the figures and a single self-contained HTML report
to S3 (`data/results/`). No local file is authoritative — anyone with the
credentials can reproduce everything from scratch.

The whole chain — **scrape → clean-and-transform → features → model** —
runs with one command (`python run_pipeline.py`), or one stage at
a time.

---

## Repository structure

```
case-predict-electricity-prices/
├── README.md                       ← you are here: the pipeline
├── run_pipeline.py                 ← run ALL 4 stages end to end
├── run_no_scrape.py                ← run everything EXCEPT scrapers (transform → features → model)
├── requirements.txt                ← Python dependencies
├── LICENSE                         ← MIT
│
├── docs/                           ← case documentation (concepts, data, scope…)
│
├── tools/                          ← stages 1–3
│   ├── upload_s3.py                ← shared S3 helper (upload + read-back + list/bytes)
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
│   ├── clean-and-transform/        ← STAGE 2: S3 data/raw/ → S3 data/processed/ (masters)
│   │   ├── run_all.py              ← run every transform in sequence
│   │   ├── transform_1_day_forecast_local_time.py   ← 24h master (forecast weather)
│   │   ├── transform_1_week_forecast_local_time.py  ← 168h master (actual weather, lag168)
│   │   └── transform_derive_available_capacity.py   ← available capacity per fuel
│   │
│   └── features/                   ← STAGE 3: masters → S3 data/processed/ (feature tables)
│       ├── run_all.py              ← run every feature builder in sequence
│       ├── feature_builder_1d.py       ← day-ahead (24h) features, lags ≥24h
│       ├── feature_builder_1w.py       ← week-ahead (168h) features, lags ≥168h
│       └── feature_builder_15min.py    ← 1h-ahead nowcast features, short lags
│
├── model/                          ← STAGE 4: features → S3 data/results/ (figures + report)
│   ├── run_all.py                  ← train all horizons + build & open the report
│   ├── model_builder_1d.py         ← 24h: 4 models vs ЕСО/naive
│   ├── model_builder_1w.py         ← 168h: 4 models vs naive
│   ├── model_builder_15min.py      ← 1h-ahead nowcast (+15-min ramp) vs persistence
│   └── build_report.py             ← all result PNGs → one self-contained index.html
│                                      (1d as a Bulgarian narrative, others as figure galleries)
│
└── data/                           ← provided seed data (snapshots, go stale)
```

---

## The data pipeline

```
  STAGE 1: scrape          STAGE 2: transform        STAGE 3: features        STAGE 4: model
  ───────────────          ──────────────────        ─────────────────        ──────────────
  ENTSO-E ┐
  Open-Met├► tools/scrapers/* ─► data/raw/ ─► tools/clean-and-transform/* ─► data/processed/ ─► tools/features/* ─► data/processed/ ─► model/* ─► data/results/
  IBEX    │      (→ S3)                                (→ S3)                   master_*.csv       (→ S3)             features_*.csv      (→ S3)      figures + index.html
  holidays┘
```

Every stage hands off through the **active storage backend** — each stage
reads the previous stage's output and writes its own through one helper
(`tools/upload_s3.py`). There are two interchangeable backends:

- **`s3`** *(default)* — the shared bucket; the real source of truth.
- **`local`** — a mirror under `./local_store/` with the **same key layout**
  (`data/raw/…`, `data/processed/…`, `data/results/…`). Because reads *and*
  writes go to that mirror, a fully local run **chains** stage→stage with no
  network.

The backend is chosen by (in priority order) a CLI flag, else the
`STORAGE_BACKEND` env var, else `s3`. A `.env` at the repo root is loaded
automatically (real env vars win), so you can keep `STORAGE_BACKEND`, AWS
credentials and `S3_BUCKET` there. All scripts follow the same conventions:

- **`--local` / `--s3`** — force the local or S3 backend for that run,
  overriding `.env`. With no flag you get the `.env` default (or `s3`).
- **`run_all.py`** — an orchestrator per stage. It runs each script as its
  own subprocess, so one failure (missing API key, network blip, missing
  input) is logged and skipped rather than aborting the batch. It forwards
  any arguments to every step (so `run_all.py --local` runs the whole stage
  on the local backend) and exits with the number of failed steps.
- **`run_pipeline.py`** — the top-level orchestrator that chains all four
  stage `run_all.py`s in order. `--from STAGE` resumes mid-pipeline (handy
  since scraping is the slow part), `--local`/`--s3` pick the backend,
  `--no-open` is forwarded only to the model stage.
- **`run_no_scrape.py`** — runs everything **except** the scrapers
  (transform → features → model), in order, on top of the raw data already
  in the backend. Uses the default backend (or `--local`/`--s3`); opens the
  report at the end unless `--no-open`.

Each script also runs perfectly well on its own — the `run_all.py`s and
`run_pipeline.py` are just convenience wrappers.

---

## Quickstart

```bash
# 1. Install dependencies (from the project root)
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt          # scrapers + feature/model stack (sklearn, xgboost, …)
playwright install chromium              # only needed for the IBEX scraper

# 2. Configure the backend (see "Configuration" below). Easiest: copy the
#    template and edit it — .env is loaded automatically.
cp .env.example .env        # then set S3_BUCKET + AWS creds (or STORAGE_BACKEND=local)
#    …or export them instead of using .env:
export AWS_ACCESS_KEY_ID=...   AWS_SECRET_ACCESS_KEY=...   AWS_DEFAULT_REGION=eu-central-1
export S3_BUCKET=your-bucket-name

# 3. Set the ENTSO-E token (free; takes a few working days to obtain)
export ENTSOE_API_KEY=your-token-here

# 4. Run the WHOLE pipeline (scrape → transform → features → model + report)
#    S3 is the default — no flag needed.
python run_pipeline.py
#    …or run it one stage at a time:
python tools/scrapers/run_all.py            # STAGE 1 → data/raw/
python tools/clean-and-transform/run_all.py # STAGE 2 → data/processed/ (masters)
python tools/features/run_all.py            # STAGE 3 → data/processed/ (features)
python model/run_all.py                     # STAGE 4 → data/results/ (figures + report)
```

`run_pipeline.py --from features` resumes from a later stage (skips the
slow scrape). Add `--local` to run entirely on the local backend
(`./local_store/`) — reads *and* writes stay local, so it still chains
end-to-end without any S3 access.

If the scrapers have already published `data/raw/` to S3 and you just want
to rebuild everything on top of it, use the dedicated wrapper (S3 by
default — no flag needed):

```bash
python run_no_scrape.py            # transform → features → model, via S3
python run_no_scrape.py --local    # same, entirely on the local backend (./local_store/)
```

---

## Stage 1 — Scrapers (`tools/scrapers/`)

Each scraper takes an optional `[START END]` window (dates `YYYY-MM-DD`)
and an optional `--local` flag. Output lands in a folder/file next to the
script, and (by default) under `data/raw/` in S3 — pass `--local` to skip
the upload.

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

These read raw inputs **back from S3** (`data/raw/`), build the canonical
masters, and (by default) push them to `data/processed/`. Order
matters — see `run_all.py`'s `STEPS`.

| Script | What it builds |
| --- | --- |
| **`transform_1_day_forecast_local_time.py`** | The **24h master**. Joins load actual + ESO load forecast (ENTSO-E), the 1-day-ahead **forecasted** weather, and the days-off calendar; canonicalises in UTC; hourly grid from 2024-02-17; `is_day_off` by **local** date; trims to where real load exists; converts to local BG time. Output: `master_hourly_long_forecasted_weather.csv`. |
| **`transform_1_week_forecast_local_time.py`** | The **168h master**. Same joins but with **actual** weather (used as `lag168` downstream, since a day-ahead forecast isn't available a week out), assembled directly in local BG time over the full load history (from ~2022-09). Output: `master_1week_long.csv`. |
| **`transform_derive_available_capacity.py`** | Hourly **available** generation capacity per fuel type — since ENTSO-E only publishes nameplate capacity yearly. `available = nameplate(year) − Σ outage MW lost`, joined 1:1 onto the generation timeline. |

> `timezone_convertor.py` (the original 24h master builder) is kept on disk
> but **superseded** by `transform_1_day_forecast_local_time.py`, which
> produces the same output and is the one wired into `run_all.py`.

### The master datasets

The two masters are the model-ready tables — one row per hour in local BG
time (`Europe/Sofia`). Both carry `load_actual_mw`, `load_forecast_mw`
(ENTSO-E / ESO), nine weather columns (`temp_c`, `wind10_ms`,
`wind100_ms`, `wind_dir_100m_deg`, `ghi_wm2`, `dni_wm2`, `cloud_pct`,
`precip_mm`, `rh_pct`) and `is_day_off`. The difference is the weather:

- **`master_hourly_long_forecasted_weather.csv`** — a **day-ahead
  forecast** for hour *T* (honest for the 24h and nowcast horizons; not
  actuals, not lagged, so no leakage).
- **`master_1week_long.csv`** — **actual** weather, consumed downstream as
  `lag168` (last week's weather is what's actually known at a 168h gate).

---

## Stage 3 — Features (`tools/features/`)

Each feature builder reads a master **back from S3**, derives the leakage-
safe predictor table for one forecast horizon, and (by default) pushes
it to `data/processed/`. Each adds load lags, weather, and calendar blocks
honest to its gate, and prints an ADF stationarity check.

| Script | Horizon | Builds | Key idea |
| --- | --- | --- | --- |
| **`feature_builder_1d.py`** | 24h (day-ahead) | `features_1h_long.csv` (or `…_diff24.csv` if the level is non-stationary) | Load lags **≥24h**, day-ahead forecast weather for *T*, calendar. |
| **`feature_builder_1w.py`** | 168h (week-ahead) | `features_1week_long.csv` | Load lags **≥168h**, weather as `lag168`, calendar. Needs ~1y warmup (`lag8760`). |
| **`feature_builder_15min.py`** | 1h-ahead nowcast | `features_1h_ahead_long.csv` | **Short** lags (`lag1`=persistence, `lag2/3`, `diff1`) — the source of skill. Reuses the **24h master** (no separate transform). |

---

## Stage 4 — Model (`model/`)

Each model builder reads a feature table **back from S3**, runs a
walk-forward (rolling-origin) evaluation of four models
(Ridge / Lasso / ElasticNet / XGBoost) against a horizon-appropriate
benchmark, and (by default) pushes its figures to
`data/results/<horizon>/`. `build_report.py` then pulls every result PNG
back from S3 and bakes them into one **self-contained** `index.html`
(images base64-embedded), uploaded to `data/results/index.html`.

| Script | Horizon | Benchmark | Figures → `data/results/…` |
| --- | --- | --- | --- |
| **`model_builder_1d.py`** | 24h | ЕСО + naive(lag24) | `1d/` — metrics, significance, per-model corr ×4, diagnostics, intervals, selection, learning curve, final |
| **`model_builder_1w.py`** | 168h | naive(lag168) | `1week/` — same set (selection picks the model) |
| **`model_builder_15min.py`** | 1h-ahead | persistence(lag1) | `15min/` — same set **+** a synthetic 15-min ramp plot |
| **`build_report.py`** | — | — | `index.html` — all of the above in one page |

`build_report.py` renders the **1d** horizon not as a flat gallery but as a
self-contained **Bulgarian narrative** that follows the Layer-1 story —
*what we test → which data → which method → results → takeaway* — with the
1d figures embedded at the right points. The page stays a single vertical
scroll (not a sideways slide deck). The `1week` / `15min` horizons follow as
ordered figure galleries. It pulls the PNGs from S3, or falls back to the
local `results/**/*.png` when S3 isn't configured.

`model/run_all.py` runs the three builders then `build_report.py`, and
**opens the report** in your browser when done (`--no-open` to skip, e.g.
on a headless box). Conformal 90% prediction intervals are Mondrian
(per-hour) calibrated; the 1h-ahead "15-min ramp" is an anchored
*assumption* (linear steps from the real current value to the 1h forecast),
clearly **not** a measured 15-min skill — there is no real 15-min load.

---

## Configuration

`tools/upload_s3.py` handles all storage I/O. For the S3 backend it uses
the standard AWS credential chain (env vars, `~/.aws/credentials`, or an IAM
role) — no browser flow, nothing to refresh. Config can live in env vars or
a `.env` at the repo root (loaded automatically; real env vars win — copy
`.env.example` to `.env` to start).

| Variable | Purpose |
| --- | --- |
| `STORAGE_BACKEND` | `s3` (default) or `local` — which backend to use |
| `LOCAL_STORE` | Local-backend mirror root; default `./local_store` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS credentials (S3 backend) |
| `AWS_DEFAULT_REGION` | e.g. `eu-central-1` (S3 backend, AWS) |
| `S3_BUCKET` | Target bucket (required for the S3 backend) |
| `S3_PREFIX` | Key prefix; default `data/raw` |
| `S3_ENDPOINT_URL` | Only for R2/B2/MinIO; omit for AWS S3 |
| `S3_DELETE_LOCAL` | `1`/`true`/`yes` → delete the working copy after a successful save |
| `ENTSOE_API_KEY` | ENTSO-E token (only the ENTSO-E scraper needs it) |

The local backend needs none of the S3 vars. On the S3 backend, if boto3
isn't installed or `S3_BUCKET` isn't set, an upload is a graceful no-op
(warns, doesn't crash); a *read* will raise asking you to configure S3 or
use `--local`.

---

## What the pipeline feeds

The pipeline targets the three forecasting layers the case asks for —
**consumption** (Layer 1), **supply** (Layer 2), and **price** (Layer 3),
each at 15-minute, 24-hour, and 1-week horizons. Stage 4 currently
implements **Layer 1 (consumption/load)** at all three horizons
(`model/`); Layers 2–3 reuse the same scrape → transform → features →
model scaffolding. The conceptual framing and required deliverables live in
the docs:

- **[docs/concepts.md](docs/concepts.md)** — market concepts and terminology.
- **[docs/data.md](docs/data.md)** — data sources, access, lags, gotchas.
- **[docs/practices.md](docs/practices.md)** — evaluation, reproducibility,
  avoiding look-ahead leakage.
- **[docs/scope.md](docs/scope.md)** — required deliverables and optional directions.

---

## Licence

MIT ([LICENSE](LICENSE)).