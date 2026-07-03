"""A 級第二通道：縮幅整理 + 下影回踩 / 跳空小K MA 預判。"""

from __future__ import annotations

from typing import Literal, Optional

import pandas as pd

from src.indicators.moving_average import add_moving_averages
from src.screener.conditions import touches_ma
from src.screener.params import V1_PARAMS

GAIN_LOOKBACK = 50
MIN_GAIN_RATIO = 0.15
AMPLITUDE_LOOKBACK = 10
MAX_AMPLITUDE_RATIO = 0.12
MA20_SLOPE_LOOKBACK = 10
GAP_UP_RATIO = 0.02
SMALL_BODY_RATIO = 0.30
TOUCH_TOLERANCE = V1_PARAMS.touch_tolerance

MaTouch = Literal["ma5", "ma10", "ma20"]


def is_lower_shadow_candle(row: pd.Series) -> bool:
    """下影線長度 > 實體長度。"""
    open_p = float(row["open"])
    close_p = float(row["close"])
    low_p = float(row["low"])
    body = abs(close_p - open_p)
    lower_shadow = min(open_p, close_p) - low_p
    if lower_shadow <= 0:
        return False
    if body <= 0:
        return lower_shadow > 0
    return lower_shadow > body


def find_ma_touch(row: pd.Series, tolerance: float = TOUCH_TOLERANCE) -> Optional[MaTouch]:
    low = float(row["low"])
    for ma_col in ("ma5", "ma10", "ma20"):
        ma_val = row.get(ma_col)
        if ma_val is not None and touches_ma(low, float(ma_val), tolerance):
            return ma_col  # type: ignore[return-value]
    return None


def is_gap_up_small_body(row: pd.Series, prev_row: pd.Series) -> bool:
    prev_close = float(prev_row["close"])
    if prev_close <= 0:
        return False
    open_p = float(row["open"])
    if open_p < prev_close * (1 + GAP_UP_RATIO):
        return False
    high_p = float(row["high"])
    low_p = float(row["low"])
    close_p = float(row["close"])
    full_range = high_p - low_p
    if full_range <= 0:
        return False
    body = abs(close_p - open_p)
    return body <= full_range * SMALL_BODY_RATIO


def projects_ma5_above_ma10_on_flat(df: pd.DataFrame) -> bool:
    """模擬明日收盤 = 今日收盤，檢查 MA5 是否 > MA10。"""
    if len(df) < 10:
        return False
    last = df.iloc[-1]
    close_p = float(last["close"])
    volume = float(last["volume"])
    next_ts = df.index[-1] + pd.Timedelta(days=1)
    hypothetical = pd.DataFrame(
        [{"open": close_p, "high": close_p, "low": close_p, "close": close_p, "volume": volume}],
        index=[next_ts],
    )
    extended = pd.concat([df[["open", "high", "low", "close", "volume"]], hypothetical])
    with_ma = add_moving_averages(extended)
    row = with_ma.iloc[-1]
    ma5 = row.get("ma5")
    ma10 = row.get("ma10")
    if pd.isna(ma5) or pd.isna(ma10):
        return False
    return float(ma5) > float(ma10)


def gain_ratio_n(df: pd.DataFrame, signal_idx: int, lookback: int) -> float:
    start = max(0, signal_idx - lookback + 1)
    window = df.iloc[start : signal_idx + 1]
    low_min = float(window["low"].min())
    high_max = float(window["high"].max())
    if low_min <= 0:
        return 0.0
    return (high_max - low_min) / low_min


def amplitude_ratio_n(df: pd.DataFrame, signal_idx: int, lookback: int) -> float:
    return gain_ratio_n(df, signal_idx, lookback)


def ma20_slope_n(df: pd.DataFrame, signal_idx: int, lookback: int) -> bool:
    prev_idx = signal_idx - lookback
    if prev_idx < 0:
        return False
    ma_now = df.iloc[signal_idx]["ma20"]
    ma_prev = df.iloc[prev_idx]["ma20"]
    if pd.isna(ma_now) or pd.isna(ma_prev):
        return False
    return float(ma_now) > float(ma_prev)


def passes_entry_shadow_retest(row: pd.Series) -> bool:
    return is_lower_shadow_candle(row) and find_ma_touch(row) is not None


def passes_entry_gap_projection(df: pd.DataFrame, signal_idx: int) -> bool:
    if signal_idx < 1:
        return False
    row = df.iloc[signal_idx]
    prev = df.iloc[signal_idx - 1]
    if not is_gap_up_small_body(row, prev):
        return False
    if float(row["ma5"]) > float(row["ma10"]):
        return False
    return projects_ma5_above_ma10_on_flat(df.iloc[: signal_idx + 1])


def passes_consolidation_strategy(df: pd.DataFrame) -> bool:
    """
  在 v1 已通過前提下，檢查新 A 級通道條件 1~5。
  進場型態：條件2（下影回踩）OR 條件3（跳空小K + MA 預判）。
  """
    if len(df) < max(120, GAIN_LOOKBACK, MA20_SLOPE_LOOKBACK + 1):
        return False

    signal_idx = len(df) - 1
    row = df.iloc[signal_idx]

    if gain_ratio_n(df, signal_idx, GAIN_LOOKBACK) <= MIN_GAIN_RATIO:
        return False

    entry_ok = passes_entry_shadow_retest(row) or passes_entry_gap_projection(df, signal_idx)
    if not entry_ok:
        return False

    if not ma20_slope_n(df, signal_idx, MA20_SLOPE_LOOKBACK):
        return False

    if amplitude_ratio_n(df, signal_idx, AMPLITUDE_LOOKBACK) >= MAX_AMPLITUDE_RATIO:
        return False

    return True
