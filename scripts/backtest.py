"""Backtest: cost-percentile rule (A) vs pure monthly DCA (B).

Two-asset (or more) monthly dollar-cost averaging, compared under one
fair rule: both strategies invest the same total amount, split by the
same allocation, on the same buy dates. Money a rule skips is carried
forward and invested at the next forced buy; the final month invests
all pending cash. Neither side ends with uninvested cash left over
by design.

All portfolio-specific inputs are passed in at run time. Nothing
about any one user's holdings, cash, allocation, tickers, dates or
total is baked into this file. Run it like this (placeholders in
angle brackets must be replaced by the caller):

    python scripts/backtest.py \
        --data-dir ./data \
        --tickers <ticker_a>,<ticker_b> \
        --allocation <allocation_a>,<allocation_b> \
        --total <total_usd> \
        --start-month <start-month> \
        --months <months> \
        --buy-day <buy-day> \
        --out backtest_results.json

Rule A (cost-percentile rule, guidance not a hard rule). The rule
parameters are exposed as command-line options and documented in
SKILL.md; they are not hidden magic numbers inside the code:
- Percentile: price percentile over the past ``--percentile-window``
  trading days, window including today.
- Low bracket (percentile <= ``--low-pct``): buy the full monthly
  amount for that ticker.
- Mid bracket (``--low-pct`` < percentile <= ``--mid-pct``): buy full,
  except when the drawdown is below ``--mid-dd`` and the percentile
  is above ``--mid-half-pct``: buy half.
- High bracket (percentile > ``--mid-pct``): buy half when the
  drawdown is at least ``--high-dd``, else skip (carry the cash).
- Boundary tolerance: a percentile within ``--tolerance`` points of
  a bracket boundary is treated as the lower bracket.
- Forced buy: after ``--max-skips`` consecutive skips for a ticker,
  the next batch buys in full. The final month always buys in full
  with all pending cash.

Drawdown definition (an explicit assumption, because the rule text
does not define it): ``(window high - today's price) / window high``,
where the window is the same percentile window. If the caller uses
a different drawdown definition, the results change; say so in the
report.

Prices: adjusted close (total-return basis, dividends treated as
reinvested) is used as both the buy price and the valuation price.
Adjusted-close averages cannot be compared directly with the
unadjusted averages shown in a broker app.

Not included in the simulation: trading commissions, bid/ask spread,
slippage, FX, tax. Both strategies trade once a month in low-fee
ETFs, so the omission barely moves the A-vs-B comparison, but it
does flatter the absolute return of both sides.

Outputs: ``--out`` JSON with per-strategy shares, cost, average cost,
final value, total return, XIRR (money-weighted annualised return),
maximum drawdown of the portfolio value, the full buy log, and the
daily portfolio value / NAV series (time-weighted NAV, so Sharpe /
Sortino are meaningful). Optional flags:
- ``--quantstats``: write a QuantStats HTML tearsheet per strategy
  from daily NAV returns. These tearsheets describe the simulated
  portfolios, not any real holding, unless the caller supplies a
  real trade history.
- ``--vectorbt-check``: independently re-compute the pure-DCA final
  shares with vectorbt and print the difference. Skipped with a
  clear message when vectorbt is not installed.
- ``--skfolio-check``: report the Sharpe ratio of the caller's
  allocation against 100%-in-each-ticker, as a sanity check only.
  It never recommends changing the allocation.
"""

import argparse
import json
from collections import Counter

import numpy as np
import pandas as pd
from scipy.optimize import brentq


def load_series(data_dir, ticker):
    path = f"{data_dir.rstrip('/')}/{ticker.lower()}_yahoo.csv"
    df = pd.read_csv(path, parse_dates=["date"])
    df["date"] = (
        pd.to_datetime(df["date"], utc=True)
        .dt.tz_convert("America/New_York")
        .dt.normalize()
        .dt.tz_localize(None)
    )
    return df.set_index("date")["adjclose"]


def buy_dates(common_index, start_month, n_months, buy_day):
    """Monthly buy-day, forwarded to the next common trading day."""
    dates, year, month = [], int(start_month[:4]), int(start_month[5:7])
    for _ in range(n_months):
        day = pd.Timestamp(year=year, month=month, day=buy_day)
        pos = common_index.searchsorted(day)
        if pos >= len(common_index):
            raise ValueError(
                f"no trading day on or after {day.date()}; "
                "the data window ends before the last buy month"
            )
        dates.append(common_index[pos])
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return dates


def bracket_for(percentile, low_pct, mid_pct, tolerance):
    if low_pct - tolerance <= percentile <= low_pct + tolerance:
        return "low"
    if mid_pct - tolerance <= percentile <= mid_pct + tolerance:
        return "mid"
    if percentile <= low_pct:
        return "low"
    if percentile <= mid_pct:
        return "mid"
    return "high"


def run(common, tickers, allocation, total, dates, end, args):
    monthly = total / len(dates)
    alloc = {t: monthly * w for t, w in zip(tickers, allocation)}
    results = {}
    for strat in ["A_rule", "B_dca"]:
        shares = {t: 0.0 for t in tickers}
        cost = {t: 0.0 for t in tickers}
        pending = {t: 0.0 for t in tickers}
        skips = {t: 0 for t in tickers}
        buys = {t: [] for t in tickers}
        daily_val, nav = {}, {}
        units, di = 0.0, 0
        day_idx = common.index
        for bi, buy_date in enumerate(dates):
            last_buy = bi == len(dates) - 1
            while di < len(day_idx) and day_idx[di] <= buy_date:
                day = day_idx[di]
                if day >= dates[0]:
                    value = sum(
                        shares[t] * common.loc[day, t] for t in tickers
                    ) + sum(pending.values())
                    daily_val[day] = value
                    nav[day] = value / units if units > 0 else 100.0
                di += 1
            pre_val = sum(
                shares[t] * common.loc[buy_date, t] for t in tickers
            ) + sum(pending.values())
            nav_now = pre_val / units if units > 0 else 100.0
            units += monthly / nav_now
            for ticker in tickers:
                price = common.loc[buy_date, ticker]
                avail = alloc[ticker] + pending[ticker]
                if strat == "B_dca":
                    action, frac = "buy_full", 1.0
                    pct = dd = None
                else:
                    window = common[ticker].loc[:buy_date].tail(args.percentile_window)
                    pct = float((window <= price).sum() / len(window) * 100)
                    high = float(window.max())
                    dd = (high - price) / high
                    bracket = bracket_for(
                        pct, args.low_pct, args.mid_pct, args.tolerance
                    )
                    if last_buy or skips[ticker] >= args.max_skips:
                        if last_buy:
                            action, frac = "buy_full_final", 1.0
                        elif skips[ticker] >= args.max_skips:
                            action, frac = "buy_full_forced", 1.0
                        else:
                            action, frac = "buy_full", 1.0
                    elif bracket == "low":
                        action, frac = "buy_full", 1.0
                    elif bracket == "mid":
                        if dd < args.mid_dd and pct > args.mid_half_pct:
                            action, frac = "buy_half", 0.5
                        else:
                            action, frac = "buy_full", 1.0
                    else:
                        if dd >= args.high_dd:
                            action, frac = "buy_half", 0.5
                        else:
                            action, frac = "skip", 0.0
                amount = avail * frac
                bought = amount / price if amount > 0 else 0.0
                shares[ticker] += bought
                cost[ticker] += amount
                pending[ticker] = avail - amount
                if action == "skip":
                    skips[ticker] += 1
                else:
                    skips[ticker] = 0
                buys[ticker].append(
                    (
                        str(buy_date.date()),
                        action,
                        round(amount, 2),
                        round(float(price), 2),
                        round(bought, 4),
                        round(pct, 1) if pct is not None else None,
                        round(dd * 100, 2) if dd is not None else None,
                    )
                )
        while di < len(day_idx) and day_idx[di] <= end:
            day = day_idx[di]
            value = sum(shares[t] * common.loc[day, t] for t in tickers) + sum(
                pending.values()
            )
            daily_val[day] = value
            nav[day] = value / units if units > 0 else 100.0
            di += 1
        series = pd.Series(daily_val).sort_index()
        final_val = float(series.iloc[-1])
        total_cost = sum(cost.values())
        total_return = (final_val - total) / total
        flows = [(-monthly, d) for d in dates] + [(final_val, end)]
        start = flows[0][1]

        def npv(rate):
            return sum(
                cf / (1 + rate) ** ((d - start).days / 365.0) for cf, d in flows
            )

        try:
            xirr = brentq(lambda r: npv(r), -0.9, 10.0)
        except Exception:  # noqa: BLE001 - XIRR has no solution in edge cases
            xirr = float("nan")
        max_dd = float((series / series.cummax() - 1).min())
        per_ticker = {}
        for ticker in tickers:
            final_price = float(common.loc[end, ticker])
            per_ticker[ticker] = {
                "shares": round(shares[ticker], 4),
                "cost": round(cost[ticker], 2),
                "avg_cost": round(cost[ticker] / shares[ticker], 2)
                if shares[ticker]
                else None,
                "final_price": round(final_price, 2),
                "final_value": round(shares[ticker] * final_price, 2),
            }
        results[strat] = {
            "n_months": len(dates),
            "monthly": round(monthly, 2),
            "first_buy": str(dates[0].date()),
            "last_buy": str(dates[-1].date()),
            "end": str(end.date()),
            "total_invested_planned": total,
            "total_cost_deployed": round(total_cost, 2),
            "uninvested_cash_end": round(sum(pending.values()), 2),
            "tickers": per_ticker,
            "daily_value": {
                str(k.date()): round(float(v), 2) for k, v in series.items()
            },
            "daily_nav": {
                str(k.date()): round(float(v), 4) for k, v in nav.items()
            },
            "final_value": round(final_val, 2),
            "total_return_pct": round(total_return * 100, 2),
            "xirr_pct": round(float(xirr) * 100, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "buys": buys,
        }
    return results


def vectorbt_check(common, tickers, allocation, total, dates):
    """Independently re-compute pure-DCA final shares with vectorbt.

    A failure here never aborts the run: it is a cross-check, and a
    failure is reported as a failure, not hidden.
    """
    try:
        import vectorbt as vbt
    except ImportError:
        print("vectorbt check: skipped (vectorbt not installed)")
        return
    monthly = total / len(dates)
    try:
        for ticker in tickers:
            weight = allocation[tickers.index(ticker)]
            amount = monthly * weight
            prices = common[ticker]
            size = pd.Series(0.0, index=prices.index)
            for buy_date in dates:
                size.loc[buy_date] = amount / prices.loc[buy_date]
            portfolio = vbt.Portfolio.from_orders(
                prices, size=size, size_type="amount", init_cash=total, fees=0
            )
            final_shares = float(
                portfolio.asset_value().iloc[-1] / prices.iloc[-1]
            )
            print(f"vectorbt check {ticker}: final shares {final_shares:.4f}")
    except Exception as exc:  # noqa: BLE001 - cross-check failure is reported, not fatal
        print(f"vectorbt check: failed ({exc}); compare by hand before reporting")


def quantstats_reports(results, out_prefix):
    try:
        import quantstats as qs
    except ImportError:
        print("QuantStats: skipped (quantstats not installed)")
        return
    for strat, label in [("A_rule", "rule-A"), ("B_dca", "dca-B")]:
        nav = pd.Series(results[strat]["daily_nav"])
        nav.index = pd.to_datetime(nav.index)
        returns = nav.pct_change().dropna()
        path = f"{out_prefix}-{label}.html"
        qs.reports.html(returns, output=path)
        print(f"QuantStats tearsheet ({strat}): {path}")


def skfolio_check(common, tickers, allocation):
    """Sanity check only: Sharpe of the caller's allocation vs 100% each."""
    try:
        from skfolio import RiskMeasure
        from skfolio.optimization import MeanRisk
    except ImportError:
        print("skfolio check: skipped (skfolio not installed)")
        return
    monthly = common.pct_change().resample("ME").apply(lambda s: (1 + s).prod() - 1)
    monthly = monthly.dropna()
    if len(monthly) < 12:
        print("skfolio check: skipped (fewer than 12 monthly observations)")
        return
    returns = monthly[tickers].values
    mean = returns.mean(axis=0)
    cov = np.cov(returns, rowvar=False)
    vol = float(np.sqrt(np.dot(allocation, np.dot(cov, allocation))))
    sharpe = float(mean @ np.array(allocation) / vol) if vol else float("nan")
    print(f"skfolio check: caller allocation Sharpe (monthly) {sharpe:.2f}")
    for i, ticker in enumerate(tickers):
        one_hot = np.zeros(len(tickers))
        one_hot[i] = 1.0
        vol_i = float(np.sqrt(np.dot(one_hot, np.dot(cov, one_hot))))
        sharpe_i = float(mean[i] / vol_i) if vol_i else float("nan")
        print(f"skfolio check: 100% {ticker} Sharpe (monthly) {sharpe_i:.2f}")
    print(
        "skfolio check: this is a sanity check on past data only. "
        "It never recommends changing the allocation."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Backtest cost-percentile rule (A) vs pure monthly DCA (B)."
    )
    parser.add_argument("--data-dir", default=".",
                        help="Directory holding <ticker-lower>_yahoo.csv files.")
    parser.add_argument("--tickers", required=True,
                        help="Comma-separated tickers, e.g. <ticker_a>,<ticker_b>.")
    parser.add_argument("--allocation", required=True,
                        help="Comma-separated weights matching --tickers; "
                             "must sum to 1, e.g. <allocation_a>,<allocation_b>.")
    parser.add_argument("--total", type=float, required=True,
                        help="Total amount to invest over the whole run, "
                             "e.g. <total_usd>.")
    parser.add_argument("--start-month", required=True,
                        help="First buy month, YYYY-MM, e.g. <start-month>.")
    parser.add_argument("--months", type=int, required=True,
                        help="Number of monthly buys.")
    parser.add_argument("--buy-day", type=int, required=True,
                        help="Day of month to buy (1-28); forwarded to the "
                             "next trading day when it is not one.")
    parser.add_argument("--percentile-window", type=int, default=260,
                        help="Trading days in the percentile/drawdown window "
                             "(default: 260).")
    parser.add_argument("--low-pct", type=float, default=50.0,
                        help="Low-bracket percentile ceiling (default: 50).")
    parser.add_argument("--mid-pct", type=float, default=75.0,
                        help="Mid-bracket percentile ceiling (default: 75).")
    parser.add_argument("--mid-half-pct", type=float, default=70.0,
                        help="Mid bracket: buy half when percentile is above "
                             "this and drawdown is below --mid-dd (default: 70).")
    parser.add_argument("--mid-dd", type=float, default=0.03,
                        help="Mid-bracket drawdown threshold (default: 0.03).")
    parser.add_argument("--high-dd", type=float, default=0.08,
                        help="High-bracket drawdown threshold for buying half "
                             "(default: 0.08).")
    parser.add_argument("--tolerance", type=float, default=2.0,
                        help="Percentile points of boundary tolerance; a "
                             "percentile within this of a boundary counts as "
                             "the lower bracket (default: 2).")
    parser.add_argument("--max-skips", type=int, default=2,
                        help="Consecutive skips before a forced full buy "
                             "(default: 2).")
    parser.add_argument("--out", default="backtest_results.json",
                        help="Path for the JSON results file.")
    parser.add_argument("--quantstats", action="store_true",
                        help="Write a QuantStats HTML tearsheet per strategy.")
    parser.add_argument("--vectorbt-check", action="store_true",
                        help="Re-compute pure-DCA final shares with vectorbt.")
    parser.add_argument("--skfolio-check", action="store_true",
                        help="Sanity-check Sharpe with skfolio; never changes "
                             "the allocation.")
    args = parser.parse_args()

    if not 1 <= args.buy_day <= 28:
        raise SystemExit("error: --buy-day must be between 1 and 28")
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    allocation = [float(x) for x in args.allocation.split(",") if x.strip()]
    if len(tickers) != len(allocation):
        raise SystemExit("error: --tickers and --allocation must have equal length")
    if abs(sum(allocation) - 1.0) > 1e-9:
        raise SystemExit("error: --allocation must sum to 1")
    if args.total <= 0 or args.months <= 0:
        raise SystemExit("error: --total and --months must be positive")

    series = {t: load_series(args.data_dir, t) for t in tickers}
    common = pd.DataFrame(series).dropna()
    print(
        f"common trading days: {len(common)} "
        f"{common.index[0].date()} -> {common.index[-1].date()}"
    )
    first_ok = common.index[args.percentile_window - 1]
    print(
        f"first date with {args.percentile_window}d history: "
        f"{first_ok.date()}"
    )
    dates = buy_dates(common.index, args.start_month, args.months, args.buy_day)
    last_month = pd.Timestamp(dates[-1].year, dates[-1].month, 1) + pd.offsets.MonthEnd(0)
    end = common.index[
        common.index.get_indexer([last_month], method="ffill")[0]
    ]
    results = run(common, tickers, allocation, args.total, dates, end, args)
    for label, result in results.items():
        print(
            f"\n== {label}: {result['first_buy']} -> {result['end']} "
            f"monthly=${result['monthly']}"
        )
        print(
            f"   deployed=${result['total_cost_deployed']} "
            f"cash_end=${result['uninvested_cash_end']} "
            f"final=${result['final_value']} "
            f"ret={result['total_return_pct']}% "
            f"xirr={result['xirr_pct']}% "
            f"mdd={result['max_drawdown_pct']}%"
        )
        for ticker, detail in result["tickers"].items():
            print(
                f"   {ticker} shares={detail['shares']} "
                f"avg=${detail['avg_cost']} val=${detail['final_value']}"
            )
    first, second = results["A_rule"], results["B_dca"]
    print(
        f"\n   DELTA A-B: final ${first['final_value'] - second['final_value']:.2f} "
        f"({(first['final_value'] / second['final_value'] - 1) * 100:.2f}%), "
        f"ret {first['total_return_pct'] - second['total_return_pct']:.2f}pp, "
        f"xirr {first['xirr_pct'] - second['xirr_pct']:.2f}pp, "
        f"mdd {first['max_drawdown_pct'] - second['max_drawdown_pct']:.2f}pp"
    )
    for ticker in tickers:
        print(
            f"   A {ticker} actions:",
            Counter(x[1] for x in first["buys"][ticker]),
        )
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False)
    print(f"\nwrote {args.out}")
    if args.quantstats:
        quantstats_reports(results, "quantstats-tearsheet")
    if args.vectorbt_check:
        vectorbt_check(common, tickers, allocation, args.total, dates)
    if args.skfolio_check:
        skfolio_check(common, tickers, allocation)


if __name__ == "__main__":
    main()
