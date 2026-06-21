"""交易模擬：隔日開盤買入、停損 -10% / 停利 +30%、最多持有 20 交易日。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Literal, Optional

import pandas as pd

MAX_HOLD_DAYS = 20
STOP_LOSS_PCT = 0.10
TAKE_PROFIT_PCT = 0.30
STRATEGY_LABEL = "停損-10%/停利+30%（最多20日）"

ExitReason = Literal["stop", "take_profit", "timeout", "fixed_exit"]

# 向後相容：前瞻追蹤結算仍以此判斷最長持有
HOLD_PERIODS = (MAX_HOLD_DAYS,)


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
    exit_reason: ExitReason = "timeout"
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


def _benchmark_return(
    benchmark_df: pd.DataFrame,
    entry_date: date,
    exit_date: date,
) -> Optional[float]:
    bench_entry_idx = _find_index(benchmark_df.index, entry_date)
    bench_exit_idx = _find_index(benchmark_df.index, exit_date)
    if bench_entry_idx is None or bench_exit_idx is None:
        return None

    bench_entry = _price_on(benchmark_df, bench_entry_idx, "open")
    bench_exit = _price_on(benchmark_df, bench_exit_idx, "close")
    if bench_entry is None or bench_exit is None:
        return None

    return (bench_exit - bench_entry) / bench_entry


def simulate_trade(
    stock_code: str,
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    signal_date: date,
    hold_days: Optional[int] = None,
) -> Optional[TradeResult]:
    """
    信號日 T → T+1 開盤買入 → 逐日檢查停損 -10% / 停利 +30%，最多 20 交易日。
    同日同時觸及：保守先判停損。hold_days 參數保留向後相容，已忽略。
    """
    del hold_days
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

    last_idx = entry_idx + MAX_HOLD_DAYS - 1
    if last_idx >= len(stock_df):
        return None

    entry_price = _price_on(stock_df, entry_idx, "open")
    if entry_price is None:
        return None

    stop_price = entry_price * (1 - STOP_LOSS_PCT)
    tp_price = entry_price * (1 + TAKE_PROFIT_PCT)
    entry_date = _to_date(dates[entry_idx])

    exit_idx = last_idx
    exit_price = _price_on(stock_df, last_idx, "close")
    exit_reason: ExitReason = "timeout"
    actual_hold = MAX_HOLD_DAYS

    if exit_price is None:
        return None

    for day_idx in range(entry_idx, entry_idx + MAX_HOLD_DAYS):
        low = _price_on(stock_df, day_idx, "low")
        high = _price_on(stock_df, day_idx, "high")
        if low is None or high is None:
            continue

        if low <= stop_price:
            exit_idx = day_idx
            exit_price = stop_price
            exit_reason = "stop"
            actual_hold = day_idx - entry_idx + 1
            break

        if high >= tp_price:
            exit_idx = day_idx
            exit_price = tp_price
            exit_reason = "take_profit"
            actual_hold = day_idx - entry_idx + 1
            break

    exit_date = _to_date(dates[exit_idx])
    bench_return = _benchmark_return(benchmark_df, entry_date, exit_date)
    if bench_return is None:
        return None

    stock_return = (exit_price - entry_price) / entry_price
    alpha = stock_return - bench_return

    return TradeResult(
        stock_code=stock_code,
        signal_date=signal_date,
        entry_date=entry_date,
        entry_price=round(entry_price, 4),
        exit_date=exit_date,
        exit_price=round(exit_price, 4),
        hold_days=actual_hold,
        return_pct=round(stock_return * 100, 2),
        benchmark_return_pct=round(bench_return * 100, 2),
        alpha_pct=round(alpha * 100, 2),
        is_win=stock_return > 0,
        beat_benchmark=alpha > 0,
        exit_reason=exit_reason,
        valid=True,
    )


def simulate_trades(
    stock_code: str,
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    signal_date: date,
    hold_periods: tuple[int, ...] = HOLD_PERIODS,
) -> List[TradeResult]:
    del hold_periods
    trade = simulate_trade(stock_code, stock_df, benchmark_df, signal_date)
    return [trade] if trade is not None else []


def simulate_fixed_exit(
    stock_code: str,
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    signal_date: date,
    exit_date: date,
) -> Optional[TradeResult]:
    """
    信號日 T → T+1 開盤買入 → 固定出場日收盤賣出（不套用停損/停利）。
    """
    if stock_df is None or stock_df.empty or benchmark_df is None or benchmark_df.empty:
        return None
    if exit_date <= signal_date:
        return None

    stock_df = stock_df.sort_index()
    dates = stock_df.index
    sig_idx = _find_index(dates, signal_date)
    if sig_idx is None:
        return None

    entry_idx = _next_trading_index(dates, sig_idx)
    if entry_idx is None:
        return None

    exit_idx = _find_index(dates, exit_date)
    if exit_idx is None or exit_idx < entry_idx:
        return None

    entry_price = _price_on(stock_df, entry_idx, "open")
    exit_price = _price_on(stock_df, exit_idx, "close")
    if entry_price is None or exit_price is None:
        return None

    entry_date = _to_date(dates[entry_idx])
    actual_exit_date = _to_date(dates[exit_idx])
    bench_return = _benchmark_return(benchmark_df, entry_date, actual_exit_date)
    if bench_return is None:
        return None

    stock_return = (exit_price - entry_price) / entry_price
    alpha = stock_return - bench_return
    hold_days = exit_idx - entry_idx + 1

    return TradeResult(
        stock_code=stock_code,
        signal_date=signal_date,
        entry_date=entry_date,
        entry_price=round(entry_price, 4),
        exit_date=actual_exit_date,
        exit_price=round(exit_price, 4),
        hold_days=hold_days,
        return_pct=round(stock_return * 100, 2),
        benchmark_return_pct=round(bench_return * 100, 2),
        alpha_pct=round(alpha * 100, 2),
        is_win=stock_return > 0,
        beat_benchmark=alpha > 0,
        exit_reason="fixed_exit",
        valid=True,
    )
