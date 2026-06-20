"""Central configuration for the Bulgarian electricity forecasting pipeline.

Everything that another module might want to tune lives here so the rest of
the code reads declaratively. See ../README.md for the modelling rationale.
"""
from __future__ import annotations

from pathlib import Path

# --- Paths -----------------------------------------------------------------
# config.py lives in solution/src/, so the case repo root is two levels up.
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
ENTSOE_DIR = DATA_DIR / "entsoe"
WEATHER_DIR = DATA_DIR / "weather"
IBEX_DIR = DATA_DIR / "ibex"

SOLUTION_DIR = REPO_ROOT / "solution"
OUTPUT_DIR = SOLUTION_DIR / "outputs"          # metrics tables, predictions
FIGURE_DIR = SOLUTION_DIR / "outputs" / "figures"

# --- Conventions -----------------------------------------------------------
# One canonical timezone for the whole project (docs/data.md: "UTC is safest").
CANONICAL_TZ = "UTC"
# The hourly layer is the workhorse: load, generation and weather are all
# hourly, so we resample the 15-min price down to hourly for these models.
HOURLY_FREQ = "1h"
QH_FREQ = "15min"

# --- Forecast horizons -----------------------------------------------------
# Expressed in *steps* of the relevant series.
HORIZONS_HOURLY = {
    "24h": 24,      # day-ahead operational horizon
    "1week": 168,   # one week ahead
}
# 15-minute horizon is only meaningful on the native 15-min price series.
HORIZON_QH_STEPS = 1  # 1 step of 15 min = 15 minutes ahead

# --- Evaluation ------------------------------------------------------------
SEED = 42
# Fraction of the (chronological) data reserved as the final, touch-once test
# set. Everything before it is available for training / walk-forward CV.
TEST_FRACTION = 0.20
# Number of expanding-window walk-forward folds used for validation reporting.
N_WALKFORWARD_FOLDS = 4

# Seasonal periods (in hourly steps) used by the seasonal-naive baseline and
# by lag features.
DAY = 24
WEEK = 168
