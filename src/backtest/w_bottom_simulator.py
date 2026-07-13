"""N漲W底假跌破 交易模擬：隔日開盤買入、次高點觸及停利／收盤跌破第二腳停損。"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

# 重用 trade_simulator.py 既有的日期/價格工具，避免再造第 4 份重複實作。
from src.backtest.trade_simulator import (
    ExitReason,
    TradeResult,
    _benchmark_return,
    _find_index,
    _next_trading_index,
    _price_on,
    _to_date,
)

MAX_HOLD_DAYS = 60  # 停利為固定價位而非固定持有期，取較寬鬆上限避免無限期持有


def simulate_w_bottom_trade(
    stock_code: str,
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    signal_date: date,
    take_profit_price: float,
    stop_loss_price: float,
    max_hold_days: int = MAX_HOLD_DAYS,
) -> Optional[TradeResult]:
    """
    信號日 T → T+1 開盤買入。
    停損：收盤價跌破第二腳最低價（stop_loss_price）。
    停利：當日高點觸及最近 50K 次高點（take_profit_price）。
    同日同時觸及：保守先判停損。逾 max_hold_days 仍未觸發則強制以收盤價出場。
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

    entry_price = _price_on(stock_df, entry_idx, "open")
    if entry_price is None:
        return None
    entry_date = _to_date(dates[entry_idx])

    last_idx = min(entry_idx + max_hold_days - 1, len(stock_df) - 1)
    if last_idx < entry_idx:
        return None

    exit_idx = last_idx
    exit_price = _price_on(stock_df, last_idx, "close")
    exit_reason: ExitReason = "timeout"
    if exit_price is None:
        return None

    for day_idx in range(entry_idx, last_idx + 1):
        close = _price_on(stock_df, day_idx, "close")
        high = _price_on(stock_df, day_idx, "high")
        if close is None or high is None:
            continue

        if close < stop_loss_price:
            exit_idx = day_idx
            exit_price = close
            exit_reason = "stop"
            break

        if high >= take_profit_price:
            exit_idx = day_idx
            exit_price = take_profit_price
            exit_reason = "take_profit"
            break

    exit_date = _to_date(dates[exit_idx])
    bench_return = _benchmark_return(benchmark_df, entry_date, exit_date)
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
        exit_date=exit_date,
        exit_price=round(exit_price, 4),
        hold_days=hold_days,
        return_pct=round(stock_return * 100, 2),
        benchmark_return_pct=round(bench_return * 100, 2),
        alpha_pct=round(alpha * 100, 2),
        is_win=stock_return > 0,
        beat_benchmark=alpha > 0,
        exit_reason=exit_reason,
        valid=True,
    )
