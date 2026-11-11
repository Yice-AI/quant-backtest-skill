# quant-backtest

Backtest a monthly DCA cost-percentile rule against pure monthly
DCA, with vectorbt and QuantStats — all inputs passed in at run
time, nothing hard-coded.

## What it does

- Compares strategy A (cost-percentile rule: buy full / buy half /
  skip by price percentile and drawdown) against strategy B (pure
  monthly DCA on fixed dates) for the same tickers, allocation,
  total amount, and buy dates.
- Produces a QuantStats HTML tearsheet per strategy (return,
  Sharpe, Sortino, maximum drawdown) from daily NAV returns.
- Sanity-checks the caller's allocation with skfolio (Sharpe of
  the allocation vs 100%-in-each-ticker, past data only). It never
  recommends changing the allocation.

## Install

```bash
python3 -m venv ~/.quant-venv
~/.quant-venv/bin/pip install -r requirements.txt
```

Do not use `pip install --user` (PEP 668 blocks it on many
systems). Do not create the venv inside `/tmp` (its tmpfs is
small and the install fills it mid-way). Use a persistent
directory such as `~/.quant-venv`.

Tested versions: vectorbt 1.2.1, quantstats 0.0.86, skfolio
1.12.9, scikit-learn 1.9.1, yfinance 1.8.0.

## Usage

All portfolio-specific values are passed in when you run it.
Replace every placeholder in angle brackets; there are no
defaults for tickers, allocation, total, start month, months,
or buy day.

```bash
# 1. Fetch daily prices (Yahoo chart API v8, adjusted close)
python scripts/fetch_prices.py \
  --tickers <ticker_a>,<ticker_b> \
  --range 10y --out-dir ./data

# 2. Run the backtest
python scripts/backtest.py \
  --data-dir ./data \
  --tickers <ticker_a>,<ticker_b> \
  --allocation <allocation_a>,<allocation_b> \
  --total <total_usd> \
  --start-month <start-month> \
  --months <months> \
  --buy-day <buy-day> \
  --out backtest_results.json \
  --quantstats --vectorbt-check --skfolio-check
```

### Parameters

| Parameter | Meaning |
|---|---|
| `--tickers` | Comma-separated tickers, e.g. `<ticker_a>,<ticker_b>` (required) |
| `--allocation` | Weights matching `--tickers`, summing to 1 (required) |
| `--total` | Total amount to invest across the whole run (required) |
| `--start-month` | First buy month, `YYYY-MM` (required) |
| `--months` | Number of monthly buys (required) |
| `--buy-day` | Day of month to buy, 1-28; forwarded to the next trading day (required) |
| `--percentile-window` | Trading days in the percentile/drawdown window (default 260) |
| `--low-pct` / `--mid-pct` | Low / mid bracket percentile ceilings (defaults 50 / 75) |
| `--mid-half-pct` / `--mid-dd` | Mid bracket: buy half when percentile is above this and drawdown is below this (defaults 70 / 0.03) |
| `--high-dd` | High bracket: buy half when drawdown is at least this (default 0.08) |
| `--tolerance` | Percentile points of boundary tolerance; within this of a boundary counts as the lower bracket (default 2) |
| `--max-skips` | Consecutive skips before a forced full buy (default 2) |
| `--quantstats` | Write a QuantStats HTML tearsheet per strategy |
| `--vectorbt-check` | Re-compute pure-DCA final shares with vectorbt |
| `--skfolio-check` | Sanity-check Sharpe with skfolio; never changes the allocation |

### The rule, in short

Percentile over the past `--percentile-window` trading days.
Low bracket: buy full. Mid bracket: buy full, except buy half
when drawdown is low and percentile is high. High bracket: buy
half on a large drawdown, else skip and carry the cash. After
`--max-skips` consecutive skips, the next batch buys in full.
The final month buys in full with all pending cash. Drawdown is
defined as `(window high - today's price) / window high`.

Fair comparison is built in: skipped money is carried forward,
the final month invests everything, and both sides invest the
same total on the same dates.

## Privacy: do not put your holdings in here

This skill is generic and intended to be public. Do not write
your holdings, share counts, cost basis, cash balances, broker
names, account details, email, or phone number into the skill,
the scripts, or any report you publish. Amounts change over
time; pass them in when you run the skill, and keep any report
that contains real positions private.

## Limitations

- A single start point is one sample, not proof. Run a second,
  longer window as a robustness check and report both.
- The drawdown definition is an assumption; changing it changes
  the result.
- Fees, bid/ask spread, slippage, FX, and tax are not modelled.
  Dividends are handled through adjusted close (treated as
  reinvested); real dividend cash arrives with tax and delay.
- QuantStats tearsheets describe the simulated portfolios. They
  are not a report on a real holding unless a full dated trade
  history is supplied.
- skfolio's sample-optimal weights are past-data results with
  overfitting risk; they are not a configuration suggestion.

## Disclaimer

This skill is not investment advice. It is a backtesting method
for comparing a timing rule against plain DCA on historical
prices. Historical results do not predict future returns.
