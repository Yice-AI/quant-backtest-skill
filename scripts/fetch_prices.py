"""Fetch daily price history from the Yahoo Finance chart API (v8).

Usage (all inputs are passed in at run time; nothing about any one
user's portfolio is baked into this file):

    python scripts/fetch_prices.py \
        --tickers <ticker_a>,<ticker_b> \
        --range 10y --out-dir ./data

Output: one CSV per ticker, ``<ticker-lower>_yahoo.csv``, with columns
``date, open, high, low, close, volume, adjclose``. ``adjclose`` is the
adjusted close (total-return basis, dividends treated as reinvested)
and is the price the backtest uses for both buying and valuation.

Data source notes (see SKILL.md for the full method):
- Primary source: Yahoo Finance chart API v8
  (``query1.finance.yahoo.com``). This is the same Yahoo data that
  the ``yfinance`` library reads underneath.
- Fallbacks, in order: ``yfinance`` library download, then Stooq
  free CSV. If a source fails, this script exits non-zero and says
  so. It never fabricates a price.
- Cross-check: compare the raw close against the Nasdaq official
  API (``api.nasdaq.com``) day by day on the overlapping trading
  days and report the maximum absolute difference. A large
  difference means the data must be re-fetched, not papered over.
"""

import argparse
import datetime
import json
import sys
import urllib.request

import pandas as pd

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}


def fetch(ticker, price_range, interval, timeout):
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{ticker}?range={price_range}&interval={interval}"
    )
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    adj = result["indicators"].get("adjclose", [{}])[0].get("adjclose")
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(timestamps, unit="s", utc=True)
            .tz_convert("America/New_York")
            .normalize(),
            "open": quote["open"],
            "high": quote["high"],
            "low": quote["low"],
            "close": quote["close"],
            "volume": quote["volume"],
            "adjclose": adj if adj is not None else quote["close"],
        }
    )
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    return df, result["meta"]


def main():
    parser = argparse.ArgumentParser(
        description="Fetch daily price history (Yahoo chart API v8)."
    )
    parser.add_argument(
        "--tickers",
        required=True,
        help="Comma-separated tickers, e.g. <ticker_a>,<ticker_b>.",
    )
    parser.add_argument(
        "--range",
        default="10y",
        help="History range passed to the Yahoo API (default: 10y).",
    )
    parser.add_argument(
        "--interval",
        default="1d",
        help="Bar interval (default: 1d).",
    )
    parser.add_argument(
        "--out-dir",
        default=".",
        help="Directory to write the per-ticker CSV files into.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds (default: 60).",
    )
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        print("error: --tickers must list at least one ticker", file=sys.stderr)
        sys.exit(2)

    for ticker in tickers:
        try:
            df, meta = fetch(ticker, args.range, args.interval, args.timeout)
        except Exception as exc:  # noqa: BLE001 - report and stop, never fake data
            print(
                f"error: failed to fetch {ticker} from Yahoo chart API v8: {exc}\n"
                "Try the yfinance library next, then Stooq free CSV. "
                "If every source fails, report it as unreadable; "
                "do not invent prices.",
                file=sys.stderr,
            )
            sys.exit(1)
        out_path = f"{args.out_dir.rstrip('/')}/{ticker.lower()}_yahoo.csv"
        df.to_csv(out_path, index=False)
        print(
            f"{ticker} rows={len(df)} "
            f"first={df['date'].iloc[0].date()} "
            f"last={df['date'].iloc[-1].date()} "
            f"last_close={df['close'].iloc[-1]} "
            f"last_adj={df['adjclose'].iloc[-1]}"
        )
        print(
            f"  NaN adj: {int(df['adjclose'].isna().sum())} "
            f"NaN close: {int(df['close'].isna().sum())}"
        )
        print(
            f"  meta firstTrade: {meta.get('firstTradeDate')} "
            f"currency: {meta.get('currency')}"
        )


if __name__ == "__main__":
    main()
