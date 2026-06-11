# Best practices

These are recommendations, not requirements. Follow them, ignore them,
or override them — but if you ignore them, do it deliberately and be
prepared to defend the choice.

## Workflow

- **Two repositories, minimum.** This case repository is read-only.
  Your team needs at least one of your own: a working repository for
  code, possibly a separate one for the final deliverable. A common
  pattern is: one repo for the messy exploratory work, one for the
  clean reproducible pipeline.
- **Use branches for experiments.** Don't all push to `main`. Pick a
  simple branching convention (one branch per feature, or per
  person, or per modelling experiment) and stick to it.
- **Commit often, commit small.** "End-of-day commit" is fine.
  "End-of-week commit with 4000 lines changed" is not.
- **README the room.** Each of your repositories should have a README
  that explains what's in it, how to run it, and what's the entry
  point. Future-you will thank present-you.

## Data management

- **Keep raw data raw.** A `data/raw/` directory where files land
  from scrapers, untouched. A separate `data/processed/` for what
  your cleaning pipeline produces. Never edit raw files by hand.
- **Pin the snapshot date.** When you scrape, record when. Filenames
  with ISO dates work fine; a tiny manifest file (CSV or JSON)
  listing source / fetched-at / row count works better.
- **Don't commit large data files to git.** A few hundred kilobytes
  is fine; anything bigger should be elsewhere (a shared drive, S3,
  external storage) with a pointer in your repository. The case is
  about modelling, not about overflowing git.
- **Document units in column names or in a schema file.**
  `load_mw`, `price_eur_per_mwh`, `temperature_c`, etc. Avoid bare
  `value`.

## Reproducibility

- **One environment file.** A `requirements.txt`, `environment.yml`,
  or `pyproject.toml`. Anyone should be able to recreate your runtime
  in one command.
- **Seed everything.** Random seeds for train/test splits, model
  initialisation, anything stochastic. Without seeds, results aren't
  reproducible and aren't comparable across experiments.
- **Make the pipeline runnable end-to-end.** From raw data to final
  predictions, with one or a small number of commands. Ad-hoc Jupyter
  cells that need to be run in a specific order are a liability.
- **Notebooks are for exploration, scripts are for production.**
  Move anything you'll re-run into a script or module.

## Modelling discipline

- **Baselines before models.** Persistence and seasonal-naïve before
  anything fancy. Compute their metrics on the same test set.
- **No future information.** When you build a feature for time t,
  every component must be available *at or before* time t. ENTSO-E's
  publication lag means yesterday's actual load may not have been
  available at the time you would have made yesterday's forecast.
  Be careful.
- **Validate forward.** Walk-forward cross-validation, expanding
  window. Train on `[start, t]`, validate on `[t, t+h]`, slide
  forward.
- **One held-out test set.** Define it before you start modelling,
  don't touch it until you're done. If you touch it more than once,
  you no longer have a test set.
- **Multiple metrics.** MAE, RMSE, MAPE for point forecasts. Pinball
  / quantile losses for probabilistic. Plot predicted vs. actual as
  a scatter.
- **Residual analysis.** Plot residuals over time, by hour, by day
  of week, by season, by weather regime. If you see structure in the
  residuals, you have features left to engineer.

## Communication

- **Visualise more than you tabulate.** Line plots of forecasts vs.
  actuals, residual plots, feature importance plots. Tables are for
  reference; plots are for understanding.
- **Honest error bars.** A point forecast with no uncertainty
  estimate is a hand-wave. Bootstrap, conformal prediction, or
  proper probabilistic forecasting — pick one.
- **Tell the story of what didn't work.** A presentation that only
  shows successes raises more questions than it answers. Showing
  what you tried and discarded, with reasons, builds credibility.
- **Conclude on what you'd do with more time.** Identifies you as
  someone who understands the problem isn't fully solved (it isn't)
  and has thought about what comes next.

## Team dynamics

- **Talk to each other every day.** A 10-minute standup costs
  nothing and prevents two people from independently solving the
  same problem in different ways.
- **Agree on conventions early.** Variable naming, timezone, file
  organisation, metric definitions. Disagreements that surface on
  day 1 are cheap; the same disagreements on day 5 are expensive.
- **Pair on the hard parts.** Joining datasets across timezones,
  evaluation protocol design, the supply-stack model — these benefit
  from two pairs of eyes.
- **Rotate the boring work.** Data cleaning isn't anyone's favourite
  task. Sharing it stops one person from becoming a bottleneck and
  another from coasting.

## When something seems too good to be true

It is. The two failure modes that produce suspiciously-good results
in this domain:

1. **Look-ahead bias.** A feature contains information from the
   future. Common culprits: using actual load when you should have
   used forecast load; computing rolling means that include the
   target period; "predicting" tomorrow using today's day-ahead
   price, which was set after tomorrow's prices became partially
   known.
2. **Data leakage in cross-validation.** Random splits across time;
   normalising features using statistics computed on the whole
   dataset including the test portion.

If your model dramatically outperforms persistence on 15-minute price,
audit your feature pipeline before celebrating. You almost certainly
have a leak.
