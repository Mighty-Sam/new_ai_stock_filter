"""止損/止盈組合交易模擬。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Literal, Optional

import pandas as pd

from src.screener.conditions import ScreenResult

MAX_HOLD_DAYS = 20

StopLossType = Literal["cross_ma", "pct_5", "pct_10"]
TakeProfitType = Literal["pct_10", "pct_20", "pct_30"]
ExitReason = Literal["stop", "take_profit", "timeout"]

STOP_LOSS_TYPES: tuple[StopLossType, ...] = ("cross_ma", "pct_5", "pct_10")
TAKE_PROFIT_TYPES: tuple[TakeProfitType, ...] = ("pct_10", "pct_20", "pct_30")

STOP_LABELS = {
    "cross_ma": "上穿均價",
    "pct_5": "-5%",
    "pct_10": "-10%",
}
TP_LABELS = {
    "pct_10": "+10%",
    "pct_20": "+20%",
    "pct_30": "+30%",
}


@dataclass
class SlTpTradeResult:
    stock_code: str
    signal_date: date
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    hold_days: int
    return_pct: float
    stop_type: StopLossType
    tp_type: TakeProfitType
    exit_reason: ExitReason
    stop_price: float
    tp_price: float
    cross_ref_price: Optional[float]
    is_win: bool
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


def cross_ref_price(stock_df: pd.DataFrame, golden_cross_date: pd.Timestamp) -> Optional[float]:
    """上穿當日 (MA5 + MA10) / 2。"""
    idx = _find_index(stock_df.index, golden_cross_date.date())
    if idx is None:
        return None
    row = stock_df.iloc[idx]
    ma5, ma10 = row.get("ma5"), row.get("ma10")
    if pd.isna(ma5) or pd.isna(ma10) or ma5 <= 0 or ma10 <= 0:
        return None
    return float((ma5 + ma10) / 2)


def _resolve_stop_price(
    stop_type: StopLossType,
    entry_price: float,
    cross_ref: Optional[float],
) -> Optional[float]:
    if stop_type == "cross_ma":
        return cross_ref
    if stop_type == "pct_5":
        return entry_price * 0.95
    if stop_type == "pct_10":
        return entry_price * 0.90
    return None


def _resolve_tp_price(tp_type: TakeProfitType, entry_price: float) -> float:
    if tp_type == "pct_10":
        return entry_price * 1.10
    if tp_type == "pct_20":
        return entry_price * 1.20
    return entry_price * 1.30


def simulate_sl_tp_trade(
    stock_code: str,
    stock_df: pd.DataFrame,
    signal: ScreenResult,
    stop_type: StopLossType,
    tp_type: TakeProfitType,
    max_hold_days: int = MAX_HOLD_DAYS,
) -> Optional[SlTpTradeResult]:
    """
    信號日 T → T+1 開盤買入 → 逐日檢查止損/止盈，最多持有 max_hold_days 交易日。
    同日同時觸及：保守先判止損。
    """
    if stock_df is None or stock_df.empty:
        return None

    stock_df = stock_df.sort_index()
    signal_date = signal.signal_date.date()
    sig_idx = _find_index(stock_df.index, signal_date)
    if sig_idx is None:
        return None

    entry_idx = _next_trading_index(stock_df.index, sig_idx)
    if entry_idx is None:
        return None

    last_idx = entry_idx + max_hold_days - 1
    if last_idx >= len(stock_df):
        return None

    entry_price = _price_on(stock_df, entry_idx, "open")
    if entry_price is None:
        return None

    cross_ref = cross_ref_price(stock_df, signal.golden_cross_date)
    stop_price = _resolve_stop_price(stop_type, entry_price, cross_ref)
    if stop_price is None or stop_price <= 0:
        return None

    tp_price = _resolve_tp_price(tp_type, entry_price)
    entry_date = _to_date(stock_df.index[entry_idx])

    exit_idx = last_idx
    exit_price = _price_on(stock_df, last_idx, "close")
    exit_reason: ExitReason = "timeout"
    hold_days = max_hold_days

    if exit_price is None:
        return None

    for day_idx in range(entry_idx, entry_idx + max_hold_days):
        low = _price_on(stock_df, day_idx, "low")
        high = _price_on(stock_df, day_idx, "high")
        if low is None or high is None:
            continue

        if low <= stop_price:
            exit_idx = day_idx
            exit_price = stop_price
            exit_reason = "stop"
            hold_days = day_idx - entry_idx + 1
            break

        if high >= tp_price:
            exit_idx = day_idx
            exit_price = tp_price
            exit_reason = "take_profit"
            hold_days = day_idx - entry_idx + 1
            break

    stock_return = (exit_price - entry_price) / entry_price

    return SlTpTradeResult(
        stock_code=stock_code,
        signal_date=signal_date,
        entry_date=entry_date,
        entry_price=round(entry_price, 4),
        exit_date=_to_date(stock_df.index[exit_idx]),
        exit_price=round(exit_price, 4),
        hold_days=hold_days,
        return_pct=round(stock_return * 100, 2),
        stop_type=stop_type,
        tp_type=tp_type,
        exit_reason=exit_reason,
        stop_price=round(stop_price, 4),
        tp_price=round(tp_price, 4),
        cross_ref_price=round(cross_ref, 4) if cross_ref is not None else None,
        is_win=stock_return > 0,
        valid=True,
    )


def simulate_all_combos(
    stock_code: str,
    stock_df: pd.DataFrame,
    signal: ScreenResult,
    max_hold_days: int = MAX_HOLD_DAYS,
) -> List[SlTpTradeResult]:
    results: List[SlTpTradeResult] = []
    for stop_type in STOP_LOSS_TYPES:
        for tp_type in TAKE_PROFIT_TYPES:
            trade = simulate_sl_tp_trade(
                stock_code,
                stock_df,
                signal,
                stop_type,
                tp_type,
                max_hold_days=max_hold_days,
            )
            if trade is not None:
                results.append(trade)
    return results
