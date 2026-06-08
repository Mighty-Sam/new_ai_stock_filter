"""選股參數（v1 / v2 preset）。"""

from __future__ import annotations

from dataclasses import dataclass

LOOKBACK_BARS = 20


@dataclass(frozen=True)
class ScreenParams:
    min_gain_ratio: float = 0.10
    min_oscillation_bars: int = 3
    max_oscillation_bars: int = 10
    retest_window_after_cross: int = 5
    touch_tolerance: float = 0.01
    min_volume_shares: float = 500_000
    volume_ma_ratio: float | None = None
    volume_ma_period: int = 5
    require_close_above_retest_ma: bool = False
    require_ma5_above_ma10: bool = False
    require_close_above_ma20: bool = False
    require_ma20_slope_positive: bool = False
    ma20_slope_lookback: int = 5


V1_PARAMS = ScreenParams(
    min_gain_ratio=0.10,
    min_oscillation_bars=3,
    max_oscillation_bars=10,
    retest_window_after_cross=5,
    touch_tolerance=0.01,
    min_volume_shares=500_000,
)

V2_BASE_PARAMS = ScreenParams(
    min_gain_ratio=0.15,
    min_oscillation_bars=3,
    max_oscillation_bars=10,
    retest_window_after_cross=3,
    touch_tolerance=0.01,
    min_volume_shares=500_000,
    volume_ma_ratio=1.2,
    volume_ma_period=5,
    require_close_above_retest_ma=True,
    require_ma5_above_ma10=True,
    require_close_above_ma20=True,
    require_ma20_slope_positive=True,
    ma20_slope_lookback=5,
)


def v2_params_with_oscillation(min_oscillation_bars: int) -> ScreenParams:
    """v2 基礎 + 可調整整理期下限（參數網格用）。"""
    return ScreenParams(
        min_gain_ratio=V2_BASE_PARAMS.min_gain_ratio,
        min_oscillation_bars=min_oscillation_bars,
        max_oscillation_bars=V2_BASE_PARAMS.max_oscillation_bars,
        retest_window_after_cross=V2_BASE_PARAMS.retest_window_after_cross,
        touch_tolerance=V2_BASE_PARAMS.touch_tolerance,
        min_volume_shares=V2_BASE_PARAMS.min_volume_shares,
        volume_ma_ratio=V2_BASE_PARAMS.volume_ma_ratio,
        volume_ma_period=V2_BASE_PARAMS.volume_ma_period,
        require_close_above_retest_ma=V2_BASE_PARAMS.require_close_above_retest_ma,
        require_ma5_above_ma10=V2_BASE_PARAMS.require_ma5_above_ma10,
        require_close_above_ma20=V2_BASE_PARAMS.require_close_above_ma20,
        require_ma20_slope_positive=V2_BASE_PARAMS.require_ma20_slope_positive,
        ma20_slope_lookback=V2_BASE_PARAMS.ma20_slope_lookback,
    )
