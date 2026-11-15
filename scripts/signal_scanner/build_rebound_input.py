#!/usr/bin/env python3
"""Build rebound-backtest inputs from the raw ``data10`` daily archive.

The raw archive stores unadjusted prices and has no point-in-time index
membership. This builder produces the frozen input set consumed by
``src/services/signal_scanner/research/us_equity_rebound``:

- split back-adjustment from ``data10/splits.csv`` + ``splits_new.csv`` so a
  4:1 split no longer looks like an oversold crash;
- one candidate universe entry per ticker with at least ``--min-bars`` bars in
  the window, so mid-window listings and delistings are included instead of a
  period-end survivor list;
- an honest universe name/definition in ``universe.json`` (no S&P 500 claim);
- ``corporate_actions.json`` stays empty because prices are already adjusted.

Usage:
    python scripts/signal_scanner/build_rebound_input.py \
        --data10 local/data/data10 \
        --output local/data/rebound_converted \
        --start 2020-01-02 --end 2023-12-29
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.services.signal_scanner.research.us_equity_rebound.adjustments import (  # noqa: E402
    load_splits,
)

FIELDS = ["ticker", "volume", "open", "close", "high", "low", "window_start", "transactions"]
BENCHMARK = "SPY"


def _dated_files(data10: Path, start: str, end: str) -> List[Path]:
    files = [
        path
        for path in sorted(data10.glob("*.csv"))
        if len(path.stem) == 10 and path.stem[:4].isdigit() and start <= path.stem <= end
    ]
    if not files:
        raise ValueError(f"No dated CSVs in {data10} between {start} and {end}")
    return files


def _valid_row(row: Dict[str, str]) -> bool:
    try:
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        volume = float(row["volume"])
    except (KeyError, TypeError, ValueError):
        return False
    if min(open_price, high, low, close) <= 0 or volume < 0:
        return False
    if high < max(open_price, low, close) or low > min(open_price, high, close):
        return False
    return True


def _new_arrays() -> Dict[str, array]:
    return {
        "day": array("i"),
        "open": array("d"),
        "high": array("d"),
        "low": array("d"),
        "close": array("d"),
        "volume": array("d"),
    }


def count_bars(files: List[Path]) -> Dict[str, int]:
    """First pass: bars per ticker, so under-warmed tickers never load."""
    counts: Dict[str, int] = {}
    for path in files:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != FIELDS:
                raise ValueError(f"Unexpected schema in {path.name}")
            seen: set[str] = set()
            for row in reader:
                if not _valid_row(row):
                    continue
                ticker = row["ticker"].strip().upper()
                if ticker in seen:
                    # The raw archive repeats a few tickers within one file;
                    # keep the first record for that session.
                    continue
                seen.add(ticker)
                counts[ticker] = counts.get(ticker, 0) + 1
    return counts


def load_bars(
    files: List[Path],
    keep: set[str],
) -> Tuple[List[str], Dict[str, Dict[str, array]], Dict[str, array]]:
    """Second pass: compact per-ticker bar arrays indexed by calendar position."""
    calendar = [path.stem for path in files]
    day_index = {day: index for index, day in enumerate(calendar)}

    bars: Dict[str, Dict[str, array]] = {}
    spy_bars: Dict[str, array] = _new_arrays()

    for path in files:
        index = day_index[path.stem]
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            seen: set[str] = set()
            for row in reader:
                if not _valid_row(row):
                    continue
                ticker = row["ticker"].strip().upper()
                if ticker in seen:
                    continue
                seen.add(ticker)
                if ticker == BENCHMARK:
                    target = spy_bars
                else:
                    if ticker not in keep:
                        continue
                    target = bars.setdefault(ticker, _new_arrays())

                target["day"].append(index)
                target["open"].append(float(row["open"]))
                target["high"].append(float(row["high"]))
                target["low"].append(float(row["low"]))
                target["close"].append(float(row["close"]))
                target["volume"].append(float(row["volume"]))

    return calendar, bars, spy_bars


def back_adjust(
    days: array,
    fields: Dict[str, array],
    splits: List[Tuple[int, float]],
    calendar: List[str],
) -> None:
    """Back-adjust in place using splits within the bar range."""
    if not splits:
        return

    first_day = calendar[days[0]]
    last_day = calendar[days[-1]]
    # A split on/after the first bar and on/before the last bar creates a unit
    # break inside the series. Splits before the first bar are already baked
    # into the raw prices; splits after the last bar affect no comparison.
    splits = sorted(
        (index, ratio)
        for index, ratio in splits
        if first_day < calendar[index] <= last_day
    )
    if not splits:
        return

    factor = 1.0
    for _, ratio in splits:
        factor *= ratio

    split_pos = 0
    for position, day_index in enumerate(days):
        day = calendar[day_index]
        while split_pos < len(splits) and calendar[splits[split_pos][0]] <= day:
            factor /= splits[split_pos][1]
            split_pos += 1
        if factor == 1.0:
            continue
        for field in ("open", "high", "low", "close"):
            fields[field][position] = fields[field][position] / factor
        fields["volume"][position] = fields["volume"][position] * factor


def write_market_csv(path: Path, calendar: List[str], bars: Dict[str, Dict[str, array]]) -> int:
    rows = 0
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "security_id", "open", "high", "low", "close", "volume"])
        for ticker in sorted(bars):
            data = bars[ticker]
            for position, day_index in enumerate(data["day"]):
                writer.writerow(
                    [
                        calendar[day_index],
                        ticker,
                        f"{data['open'][position]:.6f}",
                        f"{data['high'][position]:.6f}",
                        f"{data['low'][position]:.6f}",
                        f"{data['close'][position]:.6f}",
                        f"{data['volume'][position]:.0f}",
                    ]
                )
                rows += 1
    return rows


def write_spy_csv(path: Path, calendar: List[str], spy_bars: Dict[str, array]) -> None:
    if not spy_bars.get("day"):
        raise ValueError("SPY rows are missing from data10; the market filter needs them")
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for position, day_index in enumerate(spy_bars["day"]):
            writer.writerow(
                [
                    calendar[day_index],
                    f"{spy_bars['open'][position]:.6f}",
                    f"{spy_bars['high'][position]:.6f}",
                    f"{spy_bars['low'][position]:.6f}",
                    f"{spy_bars['close'][position]:.6f}",
                    f"{spy_bars['volume'][position]:.0f}",
                ]
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build rebound backtest inputs from data10")
    parser.add_argument("--data10", required=True, help="Raw data10 directory")
    parser.add_argument("--output", required=True, help="Output input directory")
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2023-12-29")
    parser.add_argument(
        "--min-bars",
        type=int,
        default=200,
        help="Minimum bars in the window for a ticker to be able to form SMA200",
    )
    args = parser.parse_args()

    data10 = Path(args.data10).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    files = _dated_files(data10, args.start, args.end)
    calendar = [path.stem for path in files]
    print(f"Window: {calendar[0]} .. {calendar[-1]} ({len(calendar)} sessions)")

    counts = count_bars(files)
    keep = {ticker for ticker, count in counts.items() if ticker != BENCHMARK and count >= args.min_bars}
    print(f"Tickers in window: {len(counts)}; kept with >= {args.min_bars} bars: {len(keep)}")

    calendar, bars, spy_bars = load_bars(files, keep)
    print(f"Loaded {len(bars)} tickers and {sum(len(v['day']) for v in bars.values()):,} bars")

    split_rows = load_splits(data10)
    splits_by_ticker: Dict[str, List[Tuple[int, float]]] = {}
    day_index = {day: index for index, day in enumerate(calendar)}
    for event in split_rows:
        if event["execution_date"] not in day_index:
            continue
        splits_by_ticker.setdefault(event["ticker"], []).append(
            (day_index[event["execution_date"]], event["split_ratio"])
        )
    print(f"Split events inside the window: {sum(len(v) for v in splits_by_ticker.values())}")

    for ticker, data in bars.items():
        back_adjust(data["day"], data, splits_by_ticker.get(ticker, []), calendar)
    if spy_bars.get("day"):
        back_adjust(spy_bars["day"], spy_bars, splits_by_ticker.get(BENCHMARK, []), calendar)

    rows = write_market_csv(output / "market_data.csv", calendar, bars)
    write_spy_csv(output / "spy_data.csv", calendar, spy_bars)
    (output / "trading_calendar.json").write_text(
        json.dumps({"trading_dates": calendar}, indent=2)
    )
    universe = {
        "name": "美国全市场日线（含中途上市/退市，非标普 500 成分）",
        "definition": (
            "在回测窗口内至少有 "
            f"{args.min_bars} 个有效交易日的全部美股代码；"
            "每日再按信号日之前 20 个交易日的成交额排名和成交量下限做时点筛选"
        ),
        "source": "data10 raw daily archive",
        "membership": "point_in_time_liquidity_screen",
        "adjusted": "split-adjusted prices and share volume",
        "min_bars": args.min_bars,
        "window": {"start": calendar[0], "end": calendar[-1]},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "securities": sorted(bars),
    }
    (output / "universe.json").write_text(json.dumps(universe, indent=2))
    (output / "corporate_actions.json").write_text("[]")

    print(f"Wrote {rows:,} bars for {len(bars)} tickers to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
