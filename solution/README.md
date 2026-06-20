# Solution — Bulgarian electricity forecasting

A reproducible, end-to-end pipeline that implements the three layers of the
case (consumption → supply → price) from the provided seed data, with honest
baselines and a leakage-controlled evaluation.

> This lives inside the read-only case repo for convenience. In a real team
> setup it would be its own repository (see `../docs/practices.md`).

## Quick start

```bash
pip install -r requirements.txt          # or use an existing scientific-Python env
python -m solution.run_pipeline          # run from the repo root
```

Outputs are written to `solution/outputs/`:

| File | What |
| --- | --- |
| `dataset_hourly.csv` | The joined, analysis-ready hourly dataset (UTC, documented units) |
| `metrics.json` | Full metrics: model vs. persistence vs. seasonal-naive, every layer × horizon |
| `metrics_summary.csv` | Flat table of the same |
| `figures/*.png` | Forecast-vs-actual plots over the start of the test set |

## What it does

### Data layer (`src/data.py`)
- Reads each raw CSV untouched from `../data/`.
- **One canonical timezone:** everything converted to **UTC** on load
  (`docs/data.md` calls this out as the single most important hygiene step).
- **One canonical resolution:** the 15-min price is downsampled to **hourly**
  (mean, because the series are MW/price) to join cleanly with the hourly load,
  generation and weather. The native 15-min price is kept separately for the
  15-minute horizon.
- Builds derived series: total generation, **net cross-border flow per
  neighbour** (import − export), and an **outage time series** (MW unavailable
  at each hour, reconstructed from the event-style REMIT notifications).
- Missing cells are left as `NaN` on purpose — the models consume NaN natively,
  and silent fills would hide real gaps.

### Feature layer (`src/features.py`)
Strict **no-look-ahead** contract. To predict `y(t+h)` every feature is
knowable at the forecast origin `t`:
- **Calendar features at the target time** (hour, day-of-week, month, weekend,
  Bulgarian holidays, cyclical encodings). Deterministic and known ahead — this
  is legitimate and is how real day-ahead models work.
- **Origin-time target lags & causal rolling stats**, all built from
  `y.shift(h)` so they only see data up to `t`. Includes same-hour-yesterday
  (`+24`) and same-hour-last-week (`+168`) anchors.
- **Origin-time exogenous drivers** (net position, outages, ENTSO-E wind/solar
  day-ahead forecasts), shifted by `h`.
- **Weather at the target time**, used as a *proxy for a weather forecast* — see
  the caveat below.

### Model layer (`src/models.py`)
- **Layer 1 — consumption:** `load_mw` from load lags + calendar + weather.
- **Layer 2 — supply:** `gen_total_mw` from generation lags + calendar + weather
  + outages + ENTSO-E wind/solar forecasts.
- **Layer 3 — price:** `price_eur_per_mwh` fed the **out-of-sample forecasts of
  Layers 1 and 2** (the layered design — price formed from forecasted demand and
  supply) plus price lags, calendar, net position and outages. The L1/L2
  forecasts injected into L3 are produced by walk-forward so they are genuinely
  out-of-sample, never in-sample fits.
- Estimator: `HistGradientBoostingRegressor` (gradient-boosted trees). Chosen
  over LightGBM so the pipeline runs on a stock sklearn install, and because it
  handles NaNs natively. **Direct multi-horizon strategy**: a separate model per
  (target, horizon), predicting `y(t+h)` directly (no recursive error build-up).
- **15-minute horizon** is evaluated on the native 15-min price series, where
  persistence is the benchmark to beat (it mostly isn't — see below).

### Evaluation layer (`src/evaluate.py`)
- **Baselines first:** persistence (`y(t+h)=y(t)`) and weekly seasonal-naive
  (`y(t+h)=` same hour, same weekday, last week).
- **Metrics:** MAE, RMSE, MAPE, sMAPE. Price crosses zero and goes negative, so
  MAE/RMSE/sMAPE are the trustworthy ones for Layer 3 (MAPE is reported but
  explodes near zero — read it with care).
- **One held-out test set:** the chronological last 20%, scored once.
- **Walk-forward** expanding-window folds power the out-of-sample L1/L2 forecasts
  fed into L3.

## Honesty notes / known limitations

These are deliberate and should be defended (or fixed) rather than hidden:

1. **Weather is reanalysis, used as a forecast proxy.** The provided weather is
   ERA5 (re)analysis, i.e. actuals, used here at the target time. A production
   system would feed an NWP *forecast*; expect the demand/supply skill reported
   here to be a mild *over*-estimate for that reason. Weather also only starts
   2026-02-01, so it is `NaN` for Dec–Jan (handled natively by the model).
2. **Price target is the ENTSO-E day-ahead price, not IBEX intraday.** IBEX
   continuous-intraday is capped at ~3 months and patchy; the day-ahead series
   is the recommended public substitute (`docs/data.md`). The 15-min day-ahead
   price exists only since Oct 2025, so the data span is short (Dec 2025 – Jun
   2026).
3. **Short history (~6 months).** No full seasonal cycle; the model cannot learn
   summer/winter regimes it has never seen. Refreshing with a longer ENTSO-E
   pull (and the hourly pre-Oct-2025 price) is the obvious next step.
4. **Price is hard.** Beating persistence on price — especially at 15 min — is
   genuinely difficult and the README of the case warns that a large win there
   usually means a leak. Treat near-persistence price skill as the *expected*
   honest result, not a failure.

## Layout

```
solution/
├── README.md
├── requirements.txt
├── run_pipeline.py          # end-to-end entry point
├── src/
│   ├── config.py            # paths, conventions, horizons, eval settings
│   ├── data.py              # loaders + joined hourly dataset
│   ├── features.py          # no-look-ahead feature engineering
│   ├── models.py            # Layers 1/2/3 + 15-min price
│   └── evaluate.py          # metrics, baselines, walk-forward
└── outputs/                 # generated artefacts (created on run)
```

## How the model stays honest about beating persistence

Same-hour-yesterday is a *very* strong anchor for load/generation, and a tree
that learns absolute levels actually under-performs it (it cannot extrapolate
the winter→spring demand decline). So each layer predicts
`anchor + alpha · correction`, and **`alpha` is chosen on a validation split
carved from the training data** (`_select_alpha`), never on the test set. When
the ML correction does not generalise, the validation step drives `alpha → 0`
and the model falls back to persistence — an honest "ML doesn't help here"
signal rather than a forced (and test-tuned) win.

## Results snapshot (held-out last 20%, MAE)

| Layer | Horizon | Model | Persistence | Seasonal-naive | Skill vs persist | alpha |
| --- | --- | --- | --- | --- | --- | --- |
| L3 price | 15 min | **10.94** | 11.61 | 28.46 | **+5.8%** | – |
| L3 price | 24 h | **22.32** | 26.43 | 30.87 | **+15.6%** | 0.5 |
| L3 price | 1 week | **25.75** | 30.87 | 30.87 | **+16.6%** | 0.7 |
| L2 gen | 24 h | **418.96** | 452.63 | 572.09 | **+7.4%** | 0.7 |
| L2 gen | 1 week | **560.65** | 572.09 | 572.09 | **+2.0%** | 0.5 |
| L1 load | 24 h | 169.10 | **133.09** | 227.45 | −27% | 1.0 |
| L1 load | 1 week | 227.45 | 227.45 | 227.45 | 0.0% | 0.0 |

Reproduce with `python -m solution.run_pipeline`; full numbers (RMSE, MAPE,
sMAPE) land in `outputs/metrics_summary.csv`. `skill_vs_persistence_MAE` in
`metrics.json` is `1 − MAE_model / MAE_persistence` (positive = better).

**Reading the table honestly:**
- **Price (the headline target) beats both baselines at all three horizons** —
  the most important and least expected result, and the one to scrutinise hardest
  for leaks. The feature audit (origin-time lags, calendar at target, day-ahead
  forecasts as exogenous inputs, L1/L2 forecasts injected out-of-sample) is in
  `features.py`/`models.py`; the gains are modest, which is what a *clean* price
  model should look like.
- **Generation beats persistence** at both horizons.
- **Load does not beat persistence at 24 h.** Same-hour-yesterday is a ~3.7%
  MAE baseline; our correction helped on validation but not on the test month
  (a genuine validation/test regime mismatch). At 1 week the selector chose
  `alpha=0`, i.e. it correctly declined to deviate from persistence. With a
  longer history and a real weather *forecast* this is the layer most likely to
  improve — see limitations above.
