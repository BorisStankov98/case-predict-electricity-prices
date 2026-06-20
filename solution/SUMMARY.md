# Work summary

A short account of what was built for the Bulgarian electricity forecasting
case, the decisions behind it, the problems hit, and the results. Full detail is
in [`README.md`](README.md).

## Goal

Implement the case's three layers from the provided seed data:
**Layer 1 consumption → Layer 2 supply → Layer 3 price**, at 15-minute, 24-hour
and 1-week horizons, with honest baselines and a leakage-controlled evaluation.

## What was built

A reproducible pipeline in `solution/`, run with one command
(`python -m solution.run_pipeline`):

| Module | Responsibility |
| --- | --- |
| `src/config.py` | Paths, conventions (UTC, hourly base resolution), horizons, eval settings |
| `src/data.py` | Raw-CSV loaders + the joined hourly dataset |
| `src/features.py` | No-look-ahead feature engineering |
| `src/models.py` | Layers 1/2/3 + the native 15-min price model |
| `src/evaluate.py` | Metrics, baselines, walk-forward splits |
| `run_pipeline.py` | End-to-end entry point → metrics + plots |

## Key decisions

- **Canonical timezone = UTC; canonical resolution = hourly.** Load, generation
  and weather are hourly; the 15-min day-ahead price is downsampled to hourly to
  join cleanly, and kept native for the 15-min horizon. This resolves the
  timezone / DST / resolution-mixing traps called out in `docs/data.md`.
- **Price target = ENTSO-E day-ahead price**, not IBEX intraday (IBEX is capped
  at ~3 months and patchy; `docs/data.md` recommends day-ahead as the public
  substitute).
- **Layered design respected:** Layer 3 consumes the *out-of-sample* forecasts of
  Layers 1 and 2 (produced by walk-forward) plus price lags, calendar, net
  position and outages.
- **Direct multi-horizon strategy:** a separate model per (target, horizon), so
  there is no recursive error accumulation.
- **Estimator:** `HistGradientBoostingRegressor` (sklearn) — strong on tabular
  data, native NaN handling, no extra compiled dependency.
- **Derived features built from raw:** total generation, net cross-border flow
  per neighbour (import − export), and an hourly "unavailable MW" series
  reconstructed from the event-style REMIT outage notifications.

## Problems hit and fixed

1. **Feature misalignment.** Target lags were double-shifted, pushing the
   freshest lag ~2 days into the past — the first run had *every* model far worse
   than persistence. Fixed by rebuilding all target lags/rolling stats from a
   single origin-time view `y.shift(horizon)`.
2. **Non-stationarity.** Trees cannot extrapolate the winter→spring demand
   decline, so absolute-level models systematically over-predicted on the test
   month. Fixed by modelling the **residual over a persistence anchor**
   (`prediction = anchor + alpha · correction`).
3. **Test-peeking risk.** When tuning the shrinkage `alpha`, the honest fix was
   to **select it on a validation split carved from the training data**, never on
   the held-out test set. When the ML correction doesn't generalise, `alpha → 0`
   and the model falls back to persistence — an honest "ML doesn't help here"
   signal.

## Results (held-out last 20%, MAE)

| Layer | Horizon | Model | Persistence | Skill |
| --- | --- | --- | --- | --- |
| **Price** | 15 min | **10.94** | 11.61 | **+5.8%** |
| **Price** | 24 h | **22.32** | 26.43 | **+15.6%** |
| **Price** | 1 week | **25.75** | 30.87 | **+16.6%** |
| Generation | 24 h | **418.96** | 452.63 | **+7.4%** |
| Generation | 1 week | **560.65** | 572.09 | **+2.0%** |
| Load | 24 h | 169.10 | **133.09** | −27% |
| Load | 1 week | 227.45 | 227.45 | 0.0% |

- **Price (the headline target) beats both baselines at all three horizons** —
  modest gains, which is what a *clean* (un-leaked) price model should look like.
- **Generation beats persistence** at both horizons.
- **Load does not beat persistence** (same-hour-yesterday is a ~3.7% MAE wall);
  the `alpha=0` fallback at 1 week is the selector honestly declining to deviate.

## Outputs (generated in `solution/outputs/`)

- `dataset_hourly.csv` — joined, analysis-ready hourly dataset (UTC, documented units)
- `metrics.json` / `metrics_summary.csv` — model vs. baselines, every layer × horizon
- `figures/*.png` — forecast-vs-actual plots over the start of the test set

## Honest limitations (next steps)

- **Weather is ERA5 reanalysis used as a forecast proxy**, and only starts Feb
  2026 → the demand/supply skill is a mild over-estimate, and Dec–Jan weather is
  NaN.
- **~6-month span, no full seasonal cycle** → the model can't learn regimes it
  has never seen.
- **Highest-leverage next step:** refresh ENTSO-E to a multi-year pull (and use a
  real NWP weather *forecast*), which should most help the load layer.
