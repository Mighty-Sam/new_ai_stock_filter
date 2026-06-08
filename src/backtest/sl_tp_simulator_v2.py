"""止損/止盈 v2 交易模擬（-10%、cross 變體、+25/+30%）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

import pandas as pd

from src.screener.conditions import ScreenResult

StopLossType = Literal["pct_10", "cross_low", "cross_skip_day1"]
TakeProfitType = Literal["pct_25", "pct_30"]
ExitReason = Literal["stop", "take_profit", "timeout"]
EntryMode = Literal["next_open", "signal_close"]

STOP_LABELS = {
    "pct_10": "-10%",
    "cross_low": "上穿最低",
    "cross_skip_day1": "上穿均價(SkipD1)",
}
TP_LABELS = {
    "pct_25": "+25%",
    "pct_30": "+30%",
}
ENTRY_LABELS = {
    "next_open": "隔日開盤",
    "signal_close": "信號收盤",
}


@dataclass(frozen=True)
class SlTpConfig:
    stop_type: StopLossType
    tp_type: TakeProfitType
    max_hold_days: int = 20
    entry_mode: EntryMode = "next_open"


@dataclass
class SlTpTradeResultV2:
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
    entry_mode: EntryMode
    max_hold_days: int
    exit_reason: ExitReason
    stop_price: float
    tp_price: float
    cross_ref_price: Optional[float]
    is_win: bool
    min_oscillation: int = 0
    valid: bool = True


def _to_date(ts: pd.Timestamp) -> date:
    return pd.Timestamp(ts).date()


def _find_index(dates: pd.DatetimeIndex, target: date) -> Optional[int]:
    ts = pd.Timestamp(target)
    matches = dates.get_indexer([ts], method=None)
    if len(matches) == 0 or matches[0] < 0:
        return None
    return int(matches[0])


def _price_on(df: pd.DataFrame, idx: int, column: str) -> Optional[float]:
    if idx < 0 or idx >= len(df):
        return None
    val = df.iloc[idx][column]
    if pd.isna(val) or val <= 0:
        return None
    return float(val)


def cross_avg_price(stock_df: pd.DataFrame, golden_cross_date: pd.Timestamp) -> Optional[float]:
    idx = _find_index(stock_df.index, golden_cross_date.date())
    if idx is None:
        return None
    row = stock_df.iloc[idx]
    ma5, ma10 = row.get("ma5"), row.get("ma10")
    if pd.isna(ma5) or pd.isna(ma10) or ma5 <= 0 or ma10 <= 0:
        return None
    return float((ma5 + ma10) / 2)


def cross_low_price(stock_df: pd.DataFrame, golden_cross_date: pd.Timestamp) -> Optional[float]:
    idx = _find_index(stock_df.index, golden_cross_date.date())
    if idx is None:
        return None
    low = stock_df.iloc[idx]["low"]
    if pd.isna(low) or low <= 0:
        return None
    return float(low)


def _resolve_stop_price(
    stop_type: StopLossType,
    entry_price: float,
    stock_df: pd.DataFrame,
    signal: ScreenResult,
) -> tuple[Optional[float], Optional[float]]:
    cross_ref: Optional[float] = None
    if stop_type == "pct_10":
        return entry_price * 0.90, None
    if stop_type == "cross_low":
        cross_ref = cross_low_price(stock_df, signal.golden_cross_date)
        return cross_ref, cross_ref
    if stop_type == "cross_skip_day1":
        cross_ref = cross_avg_price(stock_df, signal.golden_cross_date)
        return cross_ref, cross_ref
    return None, None


def _resolve_tp_price(tp_type: TakeProfitType, entry_price: float) -> float:
    if tp_type == "pct_25":
        return entry_price * 1.25
    return entry_price * 1.30


def _resolve_entry(
    stock_df: pd.DataFrame,
    signal_date: date,
    entry_mode: EntryMode,
) -> tuple[Optional[int], Optional[float], Optional[date]]:
    sig_idx = _find_index(stock_df.index, signal_date)
    if sig_idx is None:
        return None, None, None

    if entry_mode == "signal_close":
        price = _price_on(stock_df, sig_idx, "close")
        if price is None:
            return None, None, None
        return sig_idx, price, _to_date(stock_df.index[sig_idx])

    entry_idx = sig_idx + 1
    if entry_idx >= len(stock_df):
        return None, None, None
    price = _price_on(stock_df, entry_idx, "open")
    if price is None:
        return None, None, None
    return entry_idx, price, _to_date(stock_df.index[entry_idx])


def simulate_sl_tp_v2(
    stock_code: str,
    stock_df: pd.DataFrame,
    signal: ScreenResult,
    config: SlTpConfig,
) -> Optional[SlTpTradeResultV2]:
    """
    依 SlTpConfig 模擬單筆交易。
    同日衝突：保守先判止損。
    cross_skip_day1 / signal_close：進場日不判止損（仍可止盈，signal_close 則進場日也不判止盈）。
    """
    if stock_df is None or stock_df.empty:
        return None

    stock_df = stock_df.sort_index()
    signal_date = signal.signal_date.date()
    entry_idx, entry_price, entry_date = _resolve_entry(
        stock_df, signal_date, config.entry_mode
    )
    if entry_idx is None or entry_price is None or entry_date is None:
        return None

    last_idx = entry_idx + config.max_hold_days - 1
    if last_idx >= len(stock_df):
        return None

    stop_price, cross_ref = _resolve_stop_price(
        config.stop_type, entry_price, stock_df, signal
    )
    if stop_price is None or stop_price <= 0:
        return None

    tp_price = _resolve_tp_price(config.tp_type, entry_price)

    exit_idx = last_idx
    exit_price = _price_on(stock_df, last_idx, "close")
    exit_reason: ExitReason = "timeout"
    hold_days = config.max_hold_days

    if exit_price is None:
        return None

    skip_stop_on_entry = (
        config.stop_type == "cross_skip_day1" or config.entry_mode == "signal_close"
    )
    skip_all_on_entry = config.entry_mode == "signal_close"

    for day_idx in range(entry_idx, entry_idx + config.max_hold_days):
        low = _price_on(stock_df, day_idx, "low")
        high = _price_on(stock_df, day_idx, "high")
        if low is None or high is None:
            continue

        check_stop = not (skip_stop_on_entry and day_idx == entry_idx)
        check_tp = not (skip_all_on_entry and day_idx == entry_idx)

        if check_stop and low <= stop_price:
            exit_idx = day_idx
            exit_price = stop_price
            exit_reason = "stop"
            hold_days = day_idx - entry_idx + 1
            break

        if check_tp and high >= tp_price:
            exit_idx = day_idx
            exit_price = tp_price
            exit_reason = "take_profit"
            hold_days = day_idx - entry_idx + 1
            break

    stock_return = (exit_price - entry_price) / entry_price

    return SlTpTradeResultV2(
        stock_code=stock_code,
        signal_date=signal_date,
        entry_date=entry_date,
        entry_price=round(entry_price, 4),
        exit_date=_to_date(stock_df.index[exit_idx]),
        exit_price=round(exit_price, 4),
        hold_days=hold_days,
        return_pct=round(stock_return * 100, 2),
        stop_type=config.stop_type,
        tp_type=config.tp_type,
        entry_mode=config.entry_mode,
        max_hold_days=config.max_hold_days,
        exit_reason=exit_reason,
        stop_price=round(stop_price, 4),
        tp_price=round(tp_price, 4),
        cross_ref_price=round(cross_ref, 4) if cross_ref is not None else None,
        is_win=stock_return > 0,
        valid=True,
    )
