---
name: quant-backtest
description: Backtest a monthly DCA cost-percentile rule against pure monthly DCA with vectorbt and QuantStats, and sanity-check the allocation with skfolio — all inputs are passed in at run time, nothing is hard-coded.
---

# Quant Backtest

Chinese keywords: 回测, 定投, 成本分位, 绩效报告, 投资组合.

## When to use this skill

Use it when the user asks to check whether a timing rule actually
helps a regular monthly investment plan, or asks for a performance
report on a simulated plan. The three jobs it covers:

1. **Backtest (vectorbt + plain pandas):** compare strategy A, a
   cost-percentile rule, against strategy B, pure monthly DCA on
   fixed dates. Report which one ended with more, at what average
   cost, and with what drawdown.
2. **Performance report (QuantStats):** turn each simulated
   portfolio's daily NAV returns into an HTML tearsheet with
   return, Sharpe, Sortino and maximum drawdown.
3. **Allocation sanity check (skfolio):** report the Sharpe ratio
   of the caller's allocation against 100%-in-each-ticker, on past
   data only. This is a check, never a recommendation to change
   the allocation.

Do not use it to invent a new active strategy, to mine factors, or
to trade. It does not trade, does not place orders, and does not
change any user configuration.

## Rule: never hard-code the user's numbers

This skill is generic and public. Every portfolio-specific value
must be passed in by the caller at run time. Never write any of
these into the skill, the scripts, or a report template:

- total amount, per-month amount, allocation weights, ticker codes,
  buy day, backtest start month, number of months;
- any holding, share count, cost basis, cash balance, or account
  detail.

Use placeholders in examples: `<total_usd>`, `<ticker_a>`,
`<ticker_b>`, `<allocation_a>`, `<allocation_b>`, `<start-month>`,
`<months>`, `<buy-day>`. If an example uses concrete tickers or
amounts, label it as an example, not as anyone's real position.
Do not paste a user's holdings into a report, a script, or a
GitHub file. Amounts change over time; they are supplied when the
skill runs.

## Parameters to ask for first

Before running anything, confirm this list with the caller. Do not
guess a missing value, and do not fill it in from a previous run.

- `<ticker_a>`, `<ticker_b>` (and any further tickers): the assets
  to test, as a comma-separated list.
- `<allocation_a>`, `<allocation_b>`: weights matching the tickers,
  summing to 1.
- `<total_usd>`: total amount to invest across the whole run.
- `<start-month>`: first buy month, `YYYY-MM`.
- `<months>`: number of monthly buys.
- `<buy-day>`: day of the month to buy (1-28). A buy that falls on
  a non-trading day moves forward to the next trading day.
- Rule parameters (defaults are the rule definition, see below):
  percentile window in trading days, low/mid bracket ceilings,
  half-buy and skip thresholds, boundary tolerance, and the number
  of consecutive skips that forces a buy. State the values used in
  the report; never leave them as hidden defaults.

## The rule being tested (strategy A)

The cost-percentile rule, as a guidance rule, not a hard rule:

- Percentile: price percentile over the past `--percentile-window`
  trading days (default 260), window including today.
- Low bracket, percentile at or below `--low-pct` (default 50):
  buy the full monthly amount for that ticker.
- Mid bracket, percentile above `--low-pct` and at or below
  `--mid-pct` (default 75): buy in full, except when the drawdown
  is below `--mid-dd` (default 0.03) and the percentile is above
  `--mid-half-pct` (default 70): buy half.
- High bracket, percentile above `--mid-pct`: buy half when the
  drawdown is at least `--high-dd` (default 0.08); else skip and
  carry the cash.
- Boundary tolerance: a percentile within `--tolerance` points
  (default 2) of a bracket boundary counts as the lower bracket.
- Forced buy: after `--max-skips` consecutive skips (default 2)
  for a ticker, the next batch buys in full. The final month
  always buys in full with all pending cash.

Drawdown definition (an explicit assumption, because the rule text
does not define it): `(window high - today's price) / window high`,
with the window equal to the percentile window. If the caller uses
a different drawdown definition, the results change; state the
definition in the report.

Fair-comparison rule, always: skipped money is carried forward and
invested at the next forced buy; the final month invests all
pending cash; both strategies invest the same total, split by the
same allocation, on the same buy dates. A rule that skips buys
must not end the run with less money invested than the DCA side.

## Data sources

- Primary source: Yahoo Finance chart API v8
  (`query1.finance.yahoo.com`), fetched by
  `scripts/fetch_prices.py`. It returns the same Yahoo data the
  `yfinance` library reads underneath. Use adjusted close
  (total-return basis, dividends treated as reinvested) as both
  the buy price and the valuation price. Adjusted-close averages
  cannot be compared directly with unadjusted averages shown in a
  broker app.
- Fallbacks, in order: the `yfinance` library download, then Stooq
  free CSV. If a source fails, report it as unreadable. Never
  fabricate a price, and never fill a gap with a cached old value
  presented as current.
- Cross-check: compare the raw close against the Nasdaq official
  API (`api.nasdaq.com`) day by day on the overlapping trading
  days and report the maximum absolute difference. A large
  difference means re-fetching, not papering over.
- Warm-up: the first buy needs a full percentile window of history
  behind it. If the data starts too late for the requested
  start month, move the start month later and say so.

## Environment

- Use a virtual environment in a persistent directory, for
  example `~/.quant-venv`:
  `python3 -m venv ~/.quant-venv` then install with
  `~/.quant-venv/bin/pip install -r requirements.txt`.
- Do not use `pip install --user`; PEP 668 blocks it on many
  systems. Do not create the venv inside `/tmp`; its tmpfs is
  small (512 MB in the environment where this skill was first
  validated) and the install fills it mid-way.
- Tested versions at first validation: vectorbt 1.2.1, quantstats
  0.0.86, skfolio 1.12.9, scikit-learn 1.9.1, yfinance 1.8.0.
  Record the exact installed versions in the report.
- The core backtest is plain pandas in `scripts/backtest.py`.
  vectorbt is used only as an independent cross-check of the
  pure-DCA result (`--vectorbt-check`); if the two disagree by
  more than rounding, stop and find the bug before reporting.

## How to run

1. Fetch prices:
   `python scripts/fetch_prices.py --tickers <ticker_a>,<ticker_b> --range 10y --out-dir ./data`
   Check the printed row counts, first/last dates, and NaN counts.
2. Run the backtest:
   `python scripts/backtest.py --data-dir ./data --tickers <ticker_a>,<ticker_b> --allocation <allocation_a>,<allocation_b> --total <total_usd> --start-month <start-month> --months <months> --buy-day <buy-day> --out backtest_results.json --quantstats --vectorbt-check --skfolio-check`
3. Read the printed summary and `backtest_results.json`. Verify
   the vectorbt cross-check matches the pandas result before
   writing any conclusion.

## Output format

- Lead with a one-page plain-language conclusion: did the rule
  buy cheaper than pure DCA, by how much, and is it worth
  continuing as written. Put facts first, views separately.
- Then the facts: the exact parameters used, data source, row
  counts and date range, both strategies' final value, total
  return, XIRR (money-weighted annualised return), average cost
  per ticker, and maximum drawdown of the portfolio value.
  Every number in the conclusion must match a number in the
  script output or the JSON file.
- Distinguish the two drawdowns: NAV maximum drawdown (what a
  lump sum held still would have felt) and portfolio-value maximum
  drawdown (what a DCA portfolio actually felt, because new money
  kept arriving in down months). XIRR and CAGR can have opposite
  signs for the same run for the same reason; explain which one
  is being quoted.
- QuantStats HTML tearsheets describe the simulated portfolios.
  They are not a report on a real holding unless the caller
  supplies a full trade history with dates. Without dated trades,
  report only a snapshot (cost, value, floating profit/loss at
  one stated date) and say plainly that no tearsheet was made
  for the real holding.
- For skfolio, report the Sharpe figures and stop there. A
  sample-optimal weight is a past-data result with overfitting
  risk; it is not a configuration suggestion. Do not recommend
  changing the allocation on its basis.

## Limitations (state these in every report)

- A single start point is one sample, not proof. Run a second,
  longer window as a robustness check and report both; a rule
  that wins in one window and loses in the other has not been
  proven.
- The drawdown definition is an assumption; changing it changes
  the result.
- Fees, bid/ask spread, slippage, FX, and tax are not modelled.
  Dividends are handled through adjusted close (treated as
  reinvested); real dividend cash arrives with tax and delay.
- This skill was first validated in a two-window run (a 24-month
  main window and a 48-month robustness window) using the method
  above. That run's outcome belongs to that run's window and
  parameters; do not quote it as a general conclusion about the
  rule.

## Prohibitions

- Do not use RD-Agent or PyBroker, and do not propose a new
  active factor or machine-learning trading strategy.
- Do not trade, do not place orders, do not change the user's
  allocation or configuration, and do not write to any Drive or
  GitHub record. Only write the local report, the HTML
  tearsheets, and the JSON results the caller asked for.
- Do not present a simulated result as a real holding's
  performance. Do not present an unverified number as verified.
  If a data source cannot be read, write that it could not be
  read.
- This skill is not investment advice. Every report carries that
  line.
