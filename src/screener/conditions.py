"""均線回踩選股條件判定。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from src.screener.params import LOOKBACK_BARS, V1_PARAMS, ScreenParams

# 向後相容常數（v1 預設值）
MIN_OSCILLATION_BARS = V1_PARAMS.min_oscillation_bars
MAX_OSCILLATION_BARS = V1_PARAMS.max_oscillation_bars
RETEST_WINDOW_AFTER_CROSS = V1_PARAMS.retest_window_after_cross
TOUCH_TOLERANCE = V1_PARAMS.touch_tolerance
MIN_GAIN_RATIO = V1_PARAMS.min_gain_ratio
MIN_VOLUME_SHARES = V1_PARAMS.min_volume_shares


@dataclass
class ScreenResult:
    stock_code: str
    signal_date: pd.Timestamp
    close: float
    gain_pct: float
    retest_ma: Literal["ma5", "ma10"]
    golden_cross_date: pd.Timestamp
    death_cross_date: pd.Timestamp
    oscillation_bars: int
    ma20: float
    ma60: float
    ma120: float
    volume: float


def touches_ma(low: float, ma: float, tolerance: float = TOUCH_TOLERANCE) -> bool:
    if ma <= 0 or pd.isna(ma):
        return False
    return abs(low - ma) / ma <= tolerance


def _is_death_cross(ma5_prev: float, ma10_prev: float, ma5: float, ma10: float) -> bool:
    return ma5_prev >= ma10_prev and ma5 < ma10


def _is_golden_cross(ma5_prev: float, ma10_prev: float, ma5: float, ma10: float) -> bool:
    return ma5_prev <= ma10_prev and ma5 > ma10


def _is_near_golden_cross(
    ma5_prev: float,
    ma10_prev: float,
    ma5: float,
    ma10: float,
    tolerance: float = TOUCH_TOLERANCE,
) -> bool:
    """MA5 尚未上穿但已逼近 MA10（如台亞 2021/11/16：39.15 vs 39.38）。"""
    if ma10 <= 0 or pd.isna(ma10):
        return False
    if ma5_prev <= ma10_prev and ma5 <= ma10 and abs(ma5 - ma10) / ma10 <= tolerance:
        return True
    return False


def _is_golden_or_near_cross(
    ma5_prev: float,
    ma10_prev: float,
    ma5: float,
    ma10: float,
    tolerance: float,
) -> bool:
    return _is_golden_cross(ma5_prev, ma10_prev, ma5, ma10) or _is_near_golden_cross(
        ma5_prev, ma10_prev, ma5, ma10, tolerance
    )


def _check_gain(window: pd.DataFrame) -> float:
    low_min = window["low"].min()
    high_max = window["high"].max()
    if low_min <= 0:
        return 0.0
    return (high_max - low_min) / low_min


def _check_ma_alignment(row: pd.Series) -> bool:
    ma20, ma60, ma120 = row["ma20"], row["ma60"], row["ma120"]
    if any(pd.isna(v) for v in (ma20, ma60, ma120)):
        return False
    return ma20 > ma60 > ma120


def _find_retest_ma(row: pd.Series, tolerance: float) -> Optional[Literal["ma5", "ma10"]]:
    low = row["low"]
    if touches_ma(low, row["ma5"], tolerance):
        return "ma5"
    if touches_ma(low, row["ma10"], tolerance):
        return "ma10"
    return None


def _volume_above_ma_ratio(
    df: pd.DataFrame,
    signal_idx: int,
    period: int,
    ratio: float,
) -> bool:
    if signal_idx < period - 1:
        return False
    window = df.iloc[signal_idx - period + 1 : signal_idx + 1]["volume"]
    avg = window.mean()
    if pd.isna(avg) or avg <= 0:
        return False
    return float(df.iloc[signal_idx]["volume"]) > avg * ratio


def _ma20_slope_positive(
    df: pd.DataFrame,
    signal_idx: int,
    lookback: int,
) -> bool:
    prev_idx = signal_idx - lookback
    if prev_idx < 0:
        return False
    ma_now = df.iloc[signal_idx]["ma20"]
    ma_prev = df.iloc[prev_idx]["ma20"]
    if pd.isna(ma_now) or pd.isna(ma_prev):
        return False
    return float(ma_now) > float(ma_prev)


def evaluate_with_params(
    df: pd.DataFrame,
    stock_code: str = "",
    params: ScreenParams = V1_PARAMS,
) -> Optional[ScreenResult]:
    """
    以最新 K 棒為訊號日，依 params 判定是否符合選股條件。
    df 需含 open/high/low/close/volume 及 ma5~ma120。
    """
    if len(df) < 120:
        return None

    df = df.dropna(subset=["ma5", "ma10", "ma20", "ma60", "ma120"])
    if len(df) < LOOKBACK_BARS:
        return None

    signal_idx = len(df) - 1
    signal_row = df.iloc[signal_idx]

    if signal_row["volume"] < params.min_volume_shares:
        return None

    if params.volume_ma_ratio is not None:
        if not _volume_above_ma_ratio(
            df, signal_idx, params.volume_ma_period, params.volume_ma_ratio
        ):
            return None

    retest_ma = _find_retest_ma(signal_row, params.touch_tolerance)
    if retest_ma is None:
        return None

    if params.require_close_above_retest_ma:
        ma_val = signal_row[retest_ma]
        if pd.isna(ma_val) or float(signal_row["close"]) <= float(ma_val):
            return None

    if params.require_ma5_above_ma10:
        if float(signal_row["ma5"]) <= float(signal_row["ma10"]):
            return None

    if params.require_close_above_ma20:
        if float(signal_row["close"]) <= float(signal_row["ma20"]):
            return None

    if params.require_ma20_slope_positive:
        if not _ma20_slope_positive(df, signal_idx, params.ma20_slope_lookback):
            return None

    if not _check_ma_alignment(signal_row):
        return None

    window_start = max(0, signal_idx - LOOKBACK_BARS + 1)
    window = df.iloc[window_start : signal_idx + 1]
    gain = _check_gain(window)
    if gain <= params.min_gain_ratio:
        return None

    golden_idx: Optional[int] = None
    for i in range(signal_idx, window_start - 1, -1):
        if i == 0:
            break
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if _is_golden_or_near_cross(
            prev["ma5"], prev["ma10"], row["ma5"], row["ma10"], params.touch_tolerance
        ):
            if i <= signal_idx <= i + params.retest_window_after_cross:
                golden_idx = i
                break

    if golden_idx is None:
        return None

    death_idx: Optional[int] = None
    for i in range(golden_idx - 1, window_start - 1, -1):
        if i == 0:
            break
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if _is_death_cross(prev["ma5"], prev["ma10"], row["ma5"], row["ma10"]):
            death_idx = i
            break

    if death_idx is None:
        return None

    if death_idx < window_start:
        return None

    oscillation = 0
    for i in range(death_idx + 1, golden_idx):
        row = df.iloc[i]
        if row["ma5"] < row["ma10"]:
            oscillation += 1

    if not (params.min_oscillation_bars <= oscillation <= params.max_oscillation_bars):
        return None

    return ScreenResult(
        stock_code=stock_code,
        signal_date=df.index[signal_idx],
        close=float(signal_row["close"]),
        gain_pct=round(gain * 100, 2),
        retest_ma=retest_ma,
        golden_cross_date=df.index[golden_idx],
        death_cross_date=df.index[death_idx],
        oscillation_bars=oscillation,
        ma20=float(signal_row["ma20"]),
        ma60=float(signal_row["ma60"]),
        ma120=float(signal_row["ma120"]),
        volume=float(signal_row["volume"]),
    )


def evaluate(df: pd.DataFrame, stock_code: str = "") -> Optional[ScreenResult]:
    """v1 選股（與重構前行為一致）。"""
    return evaluate_with_params(df, stock_code=stock_code, params=V1_PARAMS)


def evaluate_as_of(
    df: pd.DataFrame,
    as_of: pd.Timestamp,
    stock_code: str = "",
    min_volume: float = 0,
    params: ScreenParams = V1_PARAMS,
) -> Optional[ScreenResult]:
    """以指定日期為訊號日判定（供單元測試 / 回測）。"""
    subset = df[df.index <= as_of].copy()
    if subset.empty:
        return None

    if min_volume > 0 and len(subset) > 0:
        subset.iloc[-1, subset.columns.get_loc("volume")] = max(
            subset.iloc[-1]["volume"], min_volume
        )

    return evaluate_with_params(subset, stock_code=stock_code, params=params)
