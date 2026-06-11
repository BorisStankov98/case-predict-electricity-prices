# Case: Forecasting Electricity Demand, Supply, and Price in Bulgaria

> A multi-layered modelling case on Bulgaria's electricity system. Predict
> what people will consume, what producers will generate, and at what
> price the market will clear — at multiple time horizons.

This repository **defines the case**. It is read-only: the problem
statement, conceptual background, suggested approach, provided data,
and provided tooling. Your team's work — code, processed data,
intermediate results, final outputs — lives in **your own
repositories**, not here.

---

## Repository structure

```
case-bulgarian-electricity-forecasting/
├── README.md                      ← you are here: the case definition
├── LICENSE                        ← MIT
│
├── docs/                          ← detailed case documentation
│   ├── concepts.md                ← electricity-market concepts & terminology
│   ├── data.md                    ← data sources, access, and gotchas
│   ├── methods.md                 ← suggested modelling approaches per layer
│   ├── practices.md               ← best practices (workflow, evaluation, teamwork)
│   └── scope.md                   ← what's required, what's open, optional directions
│
├── tools/                         ← provided, working scrapers
│   ├── README.md                  ← how to run each scraper
│   ├── scrape_ibex_idm_15min.py   ← IBEX continuous-intraday QH prices & volumes
│   ├── scrape_entsoe_bulgaria.py  ← ENTSO-E: prices, load, generation, cross-border, outages
│   └── scrape_weather_bulgaria.py ← Open-Meteo hourly weather, 5 cities + country average
│
└── data/                          ← provided seed data (snapshots from the sources)
    ├── README.md                  ← what each file is + source quick-reference
    ├── ibex/                      ← IBEX intraday 15-minute prices & volumes
    ├── entsoe/                    ← ENTSO-E datasets for Bulgaria (one CSV per dataset)
    └── weather/                   ← hourly weather per city + country average
```

The seed data in `data/` lets you start modelling on day one. It is a
snapshot — it goes stale. Refresh from the live sources with the
provided scrapers early in your work (see `tools/README.md`; note that
the ENTSO-E scraper needs a free API token that takes a few working
days to obtain, so request it immediately).

---

## What you're solving

Electricity is unusual: it can't be stored at scale, it must be produced
and consumed simultaneously, and the price for each delivery period is
set by a market mechanism that balances forecasted supply against
forecasted demand. When forecasts are wrong, prices spike, plants
ramp inefficiently, or the system operator has to intervene.

Your task is to build models that anticipate, for the Bulgarian power
system:

1. how much electricity **will be consumed**,
2. how much **will be supplied**,
3. and at what **price** it will trade on the Bulgarian Independent
   Energy Exchange (IBEX),

at three horizons: **15 minutes ahead**, **24 hours ahead**, and
**1 week ahead**.

The case is **layered**: each layer is a complete problem in itself,
and each subsequent layer uses the output of the previous one. Strong
teams reach the higher layers; struggling teams still deliver something
meaningful at the lower ones.

---

## The three layers

### Layer 1 — Consumption forecasting

Build a multi-factor model that predicts Bulgarian electricity
consumption at the three horizons. Use, at minimum:

- **historical consumption** (load) time series,
- **weather data** (temperature drives heating and cooling; humidity
  matters; daylight matters),
- **calendar features** (hour-of-day, day-of-week, holidays, seasons),
- **cross-border physical flows** (Bulgaria's electricity exchange
  with its neighbours),

and any other data sources the team finds relevant — outage
notifications, special events, economic indicators, anything the team
can defend as causally connected.

### Layer 2 — Supply forecasting

Build a multi-factor model that predicts Bulgarian electricity
generation (total, and ideally split by production type) at the same
horizons. Use, at minimum:

- **historical generation per production type**,
- **weather** (wind speed and solar irradiance directly drive
  renewable generation),
- **unavailability of generation units** (planned and forced outages
  reduce available capacity),
- **cross-border flows**,

and other data the team finds relevant.

### Layer 3 — Price forecasting

Using the consumption forecast, the supply forecast, and historical
prices, predict the IBEX price at 15 minutes, 24 hours, and 1 week
ahead. This is the deepest layer: it is built on top of the outputs of
Layers 1 and 2 and depends on them, but you can also feed it any
feature you find useful — fuel prices, neighbour-country prices,
day-ahead prices, outages, anything you can argue for.

---

## How the layers connect (and why this order)

Electricity prices are determined by where the supply curve meets the
demand curve at the moment of clearing. If you can forecast both
curves' positions, you can forecast the clearing price. This is the
economic intuition behind the layering — it mirrors how electricity
markets actually function:

```
   Layer 1: D̂(t+h)  ─┐
                      ├──► Layer 3: P̂(t+h)
   Layer 2: Ŝ(t+h)  ─┘
                  ▲
                  │
   Historical prices, fuel prices, neighbour prices,
   outages, other contextual features
```

Layer 3 doesn't have to use Layers 1 and 2 as the only inputs — but it
must use *something* from them, otherwise you've collapsed the case
into a single-layer pure-price model and lost the conceptual point.

---

## Read these next

This README gives the framing. The detailed material is split across
several documents so you can dive into what's relevant when:

- **[docs/concepts.md](docs/concepts.md)** — concepts and terminology
  (intraday market, day-ahead market, MTU, IDA, SIDC, SDAC, NTC, load
  vs. generation vs. net position, what "15-minute resolution" means
  in this context). Read this first if anything in the layers above
  felt unfamiliar.

- **[docs/data.md](docs/data.md)** — where the data lives, what's free
  vs. paid, what's lagged, what the gotchas are.

- **[docs/methods.md](docs/methods.md)** — suggested modelling
  approaches per layer, with notes on what works, what's a sensible
  baseline, and what the literature says about realistic performance.

- **[docs/practices.md](docs/practices.md)** — best practices for
  time-series forecasting, evaluation, reproducibility, and team
  workflow. Suggestions, not commandments.

- **[docs/scope.md](docs/scope.md)** — what's in scope, what's out of
  scope, what the team is free to redefine, and an explicit list of
  optional directions for teams that finish the three layers and want
  to keep going.

- **[tools/README.md](tools/README.md)** — how to install dependencies
  and run each of the three provided scrapers.

- **[data/README.md](data/README.md)** — what's in the provided seed
  data, plus a quick-reference card of all sources, bidding-zone codes,
  and production-type codes.

---

## What's provided

- **This case description** — the README and `docs/`.
- **Seed data** in `data/` — snapshots of the three primary sources
  (IBEX intraday prices, ENTSO-E datasets for Bulgaria, hourly weather),
  so you can start modelling immediately without waiting for API access.
- **Working scrapers** in `tools/` — to refresh the seed data and to
  extend it (longer windows, more variables, neighbour countries).

The scrapers are starting points, not finished products. You will
extend them, add data sources we haven't thought of, fix things, and
combine the outputs into your own working datasets.

## What's NOT provided

- A canonical modelling dataset. You build it from the raw pieces.
- A target metric. You choose, justify, and report it.
- A reference solution. There isn't one "right answer".
- A roadmap, calendar, or checkpoint schedule. You organise your own
  work.

---

## Suggested team composition

Five people. One reasonable split — you are free to organise
differently if your team has different strengths:

| Role | Focus |
| --- | --- |
| **Data engineer** | Extending the scrapers, building the joined dataset, handling timezones, missing data, schema drift |
| **Consumption modeller** | Layer 1 — the demand model and its features |
| **Supply modeller** | Layer 2 — the generation model and its features |
| **Price modeller / integrator** | Layer 3 — the price model, plus integrating Layers 1 and 2 into it |
| **Methodologist / presenter** | Evaluation design, baselines, validation strategy, final write-up and presentation |

Roles can blur. The "methodologist" should be involved from day one,
not just at the end — designing the evaluation protocol *before* the
modellers start tuning is what separates a defensible result from a
suspicious one.

---

## A note on realistic expectations

Electricity-price forecasting at 15-minute resolution is genuinely
hard. Persistence — predicting "the next 15 minutes will look like the
last 15 minutes" — is a stubborn baseline that even sophisticated
models often fail to beat by much. This is not a flaw in the case; it
is the reality of the problem. **A team that delivers Layers 1 and 2
well, with a credible attempt at Layer 3 and an honest discussion of
where it succeeded and where it didn't, has done good work.** A team
that claims to have solved Layer 3 with stellar metrics has almost
certainly leaked information from the future into their evaluation.

See `docs/practices.md` for how to avoid that mistake.

---

## Licence

This case description, the provided tools, and the provided seed data
are released under the **MIT License** ([LICENSE](LICENSE)). You are
free to use, adapt, and republish them.

Your own work, in your own repositories, is yours to license as you
wish.
