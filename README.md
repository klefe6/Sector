# Investing Decision Cockpit — Phase 1

A swing-trading decision dashboard that sits **alongside** the existing Sector RRG tool. This phase delivers the home dashboard and trim monitor only — no scraping, no PDF parsing, no AI calls, no fundamental data replication.

> InvestingPro stays your research and fundamentals tool.
> This site owns the decision workflow, alerts, and trim discipline.

## What's in Phase 1

1. Home dashboard with a one-line "today's verdict"
2. Trim monitor — for every position, technical extension flags + a verdict
3. Alert panel generated directly from the verdicts
4. Sector rotation strip (placeholder data; wires to RRG in Phase 2)
5. Market regime tile (placeholder, no data yet)

## Files

```
decision_cockpit/
├── app.py                  Dash application (port 8060)
├── trim_monitor.py         Indicator math + flag/verdict logic
├── alerts.py               Alert and "today's verdict" generation
├── sector_status.py        Placeholder sector status (will hook into RRG later)
├── sample_positions.csv    Sample portfolio — replace with your own
├── requirements.txt        Python dependencies for this module only
└── README.md               This file
```

The existing `app_rrg.py` and its files at the repository root are untouched.

## Run locally

```bash
cd decision_cockpit
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:8060 in your browser.

The existing Sector RRG dashboard continues to run on its own port (8059) without conflict.

## Use your own positions

Edit `sample_positions.csv` or point the app at a different file:

```bash
DECISION_POSITIONS_CSV=/path/to/your/positions.csv python app.py
```

CSV columns:

| column              | required | notes                                              |
|---------------------|----------|----------------------------------------------------|
| ticker              | yes      | Yahoo Finance symbol                               |
| shares              | optional | Used for market value display in later phases      |
| cost_basis          | yes      | Average entry price                                |
| target_weight       | optional | Percent (e.g. 5.0 = 5%)                            |
| current_weight      | optional | Percent — needed for the weight-overage flag       |
| sector              | optional | Sector ETF symbol (XLK, XLF, etc.)                 |
| conviction          | optional | A / B / C — A softens verdict by one level         |
| investingpro_url    | optional | Hyperlink to the InvestingPro page                 |
| notes               | optional | Free text                                          |

## How the verdict logic works

Every position runs through this pipeline:

**1. Compute six trim flags** (all with current thresholds in `trim_monitor.py`):

| # | Flag                      | Threshold                                |
|---|---------------------------|------------------------------------------|
| 1 | Above 20 EMA              | > 8%                                     |
| 2 | Above 50 EMA              | > 12%                                    |
| 3 | RSI 14                    | > 70                                     |
| 4 | Gain from cost basis      | > 20%                                    |
| 5 | Weight over target        | current_weight > target_weight × 1.25    |
| 6 | Sector status weakening   | sector quadrant == "Weakening"           |
| 7 | Earnings soon             | placeholder, not active in Phase 1       |

**2. Initial verdict from flag count:**

| Flags | Verdict          |
|-------|------------------|
| 0–1   | Hold             |
| 2     | Watch            |
| 3     | Trim candidate   |
| 4+    | Strong trim      |

**3. Conviction A modifier.** If the position has conviction `A`, downgrade the verdict by one level — but never below `Watch` if there are 3 or more flags. (So a high-conviction name with 5 flags lands at `Trim candidate`, not `Hold`.)

**4. Sector weakening upgrade.** If sector status is `Weakening` *and* there are 3 or more flags total, upgrade by one level. Sector weakening also counts as flag #6 toward the count, so a weak sector applies pressure twice — intentionally.

**5. Alert mapping:**

| Verdict          | Alert color |
|------------------|-------------|
| Strong trim      | red         |
| Trim candidate   | amber       |
| Watch            | blue        |
| Hold             | none        |

## Edit the rules

Thresholds are module-level constants at the top of `trim_monitor.py`:

```python
PCT_ABOVE_20EMA_THRESHOLD = 8.0
PCT_ABOVE_50EMA_THRESHOLD = 12.0
RSI_THRESHOLD = 70.0
GAIN_THRESHOLD = 20.0
WEIGHT_OVERAGE_THRESHOLD = 1.25
```

Change a number, save, refresh the dashboard.

## Data reliability

- The "Updated" timestamp on the verdict banner shows when the analysis last ran
- yfinance failures are captured per-ticker and rendered as `ERROR` rows in the trim monitor — never silently dropped
- Failed tickers also appear as amber alerts so you can't miss them
- Refresh button at the top right re-runs the full analysis

## Sector status — Phase 1 vs Phase 2

For Phase 1, sector quadrants are read from a hardcoded dict in `sector_status.py`. To update them, edit `PLACEHOLDER_STATUS` to reflect the latest reading from your existing RRG dashboard.

In Phase 2, that function will be replaced with a direct call into your `app_rrg.py` calculation, so the existing tool becomes the single source of truth and you'll never have to manually sync it.

## What's deliberately NOT here

These are excluded by design — adding them now would kill the velocity of the discipline loop:

- PDF parsing of InvestingPro reports
- Scraping any external site
- Full fundamental valuation modeling
- AI-generated buy/sell recommendations
- Watchlist tab, Journal tab, Ticker review tab, Settings tab
- Earnings date integration (flag #7 is wired but inactive)

Each of these belongs to a later phase. Phase 1 ships with one job: tell you, in one sentence, what you should do today.

## Verbose change report

| Action  | File                              | Purpose                                       |
|---------|-----------------------------------|-----------------------------------------------|
| created | decision_cockpit/app.py           | Dash dashboard, port 8060, full layout        |
| created | decision_cockpit/trim_monitor.py  | yfinance fetch, indicators, flags, verdict   |
| created | decision_cockpit/alerts.py        | Alert objects + today's verdict sentence      |
| created | decision_cockpit/sector_status.py | Placeholder sector quadrant map               |
| created | decision_cockpit/sample_positions.csv | 10-row sample portfolio                   |
| created | decision_cockpit/requirements.txt | Dependencies (dash, yfinance, pandas, numpy)  |
| created | decision_cockpit/README.md        | This file                                     |
| no change | app_rrg.py                      | Existing RRG dashboard untouched              |
| no change | requirements.txt (root)         | Existing dependencies untouched               |

## Verdict logic — verified

The verdict mechanic was unit-tested against the spec with 11 cases covering flag counts 0–4, conviction A flooring at Watch, and the sector-weakening upgrade. All pass. See the comments in `trim_monitor.py::derive_verdict` for the canonical implementation.
