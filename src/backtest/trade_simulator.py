"""交易模擬：隔日開盤買入、持有 N 日後收盤賣出。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional

import pandas as pd

HOLD_PERIODS = (10, 20)


@dataclass
class TradeResult:
    stock_code: str
    signal_date: date
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    hold_days: int
    return_pct: float
    benchmark_return_pct: float
    alpha_pct: float
    is_win: bool
    beat_benchmark: bool
    valid: bool = True


def _to_date(ts: pd.Timestamp) -> date:
    return pd.Timestamp(ts).date()


def _find_index(dates: pd.DatetimeIndex, target: date) -> Optional[int]:
    ts = pd.Timestamp(target)
    matches = dates.get_indexer([ts], method=None)
    if len(matches) == 0 or matches[0] < 0:
        return None
    return int(matches[0])


def _next_trading_index(dates: pd.DatetimeIndex, after_idx: int) -> Optional[int]:
    if after_idx + 1 >= len(dates):
        return None
    return after_idx + 1


def _price_on(df: pd.DataFrame, idx: int, column: str) -> Optional[float]:
    if idx < 0 or idx >= len(df):
        return None
    val = df.iloc[idx][column]
    if pd.isna(val) or val <= 0:
        return None
    return float(val)


def simulate_trade(
    stock_code: str,
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    signal_date: date,
    hold_days: int,
) -> Optional[TradeResult]:
    """
    信號日 T → T+1 開盤買入 → 買入後第 hold_days 個交易日收盤賣出。
    """
    if stock_df is None or stock_df.empty or benchmark_df is None or benchmark_df.empty:
        return None

    stock_df = stock_df.sort_index()
    dates = stock_df.index
    sig_idx = _find_index(dates, signal_date)
    if sig_idx is None:
        return None

    entry_idx = _next_trading_index(dates, sig_idx)
    if entry_idx is None:
        return None

    exit_idx = entry_idx + hold_days - 1
    if exit_idx >= len(stock_df):
        return None

    entry_price = _price_on(stock_df, entry_idx, "open")
    exit_price = _price_on(stock_df, exit_idx, "close")
    if entry_price is None or exit_price is None:
        return None

    entry_date = _to_date(dates[entry_idx])
    exit_date = _to_date(dates[exit_idx])

    bench_entry_idx = _find_index(benchmark_df.index, entry_date)
    bench_exit_idx = _find_index(benchmark_df.index, exit_date)
    if bench_entry_idx is None or bench_exit_idx is None:
        return None

    bench_entry = _price_on(benchmark_df, bench_entry_idx, "open")
    bench_exit = _price_on(benchmark_df, bench_exit_idx, "close")
    if bench_entry is None or bench_exit is None:
        return None

    stock_return = (exit_price - entry_price) / entry_price
    bench_return = (bench_exit - bench_entry) / bench_entry
    alpha = stock_return - bench_return

    return TradeResult(
        stock_code=stock_code,
        signal_date=signal_date,
        entry_date=entry_date,
        entry_price=round(entry_price, 4),
        exit_date=exit_date,
        exit_price=round(exit_price, 4),
        hold_days=hold_days,
        return_pct=round(stock_return * 100, 2),
        benchmark_return_pct=round(bench_return * 100, 2),
        alpha_pct=round(alpha * 100, 2),
        is_win=stock_return > 0,
        beat_benchmark=alpha > 0,
        valid=True,
    )


def simulate_trades(
    stock_code: str,
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    signal_date: date,
    hold_periods: tuple[int, ...] = HOLD_PERIODS,
) -> List[TradeResult]:
    results: List[TradeResult] = []
    for days in hold_periods:
        trade = simulate_trade(stock_code, stock_df, benchmark_df, signal_date, days)
        if trade is not None:
            results.append(trade)
    return results
