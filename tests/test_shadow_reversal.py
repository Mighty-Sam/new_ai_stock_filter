"""長下影線反轉選股單元測試。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.indicators.moving_average import add_moving_averages
from src.screener.shadow_reversal import (
    SHADOW_LOOKBACK_MIN_GAIN_PCT,
    SHADOW_LOWER_SHADOW_MIN_RATIO,
    evaluate_shadow_reversal,
)


def _build_df(
    n_base: int = 100,
    n_rally: int = 21,
    rally_start: float = 61.0,
    rally_end: float = 89.0,
    signal_open: float = 88.0,
    signal_high: float = 89.0,
    signal_low: float = 70.0,
    signal_close: float = 87.0,
    signal_volume: float = 2_000_000.0,
    prior_bar_volume: float = 1_500_000.0,
) -> pd.DataFrame:
    """基期橫盤 + 一段急拉波段 + 尾根長下影線反轉K棒。"""
    rows = []
    for _ in range(n_base):
        rows.append({"open": 60.0, "high": 60.3, "low": 59.7, "close": 60.0, "volume": 4_000_000.0})

    rally_closes = np.linspace(rally_start, rally_end, n_rally)
    for i, c in enumerate(rally_closes):
        vol = prior_bar_volume if i == n_rally - 1 else 4_000_000.0
        rows.append({"open": c - 0.5, "high": c + 0.3, "low": c - 0.6, "close": c, "volume": vol})

    rows.append(
        {
            "open": signal_open,
            "high": signal_high,
            "low": signal_low,
            "close": signal_close,
            "volume": signal_volume,
        }
    )

    idx = pd.bdate_range("2024-01-02", periods=len(rows))
    return add_moving_averages(pd.DataFrame(rows, index=idx))


def test_evaluate_pass():
    df = _build_df()
    result = evaluate_shadow_reversal(df, stock_code="9999")
    assert result is not None
    assert result.stock_code == "9999"
    assert result.lower_shadow_ratio >= SHADOW_LOWER_SHADOW_MIN_RATIO
    assert result.lookback_gain_pct > SHADOW_LOOKBACK_MIN_GAIN_PCT
    assert set(result.touched_mas) >= {"ma5", "ma10", "ma20"}
    assert result.stop_loss_price == 70.0
    assert result.take_profit_pct == 20.0
    assert result.volume_increased is True


def test_shadow_too_short():
    # 下影線只佔全距 20%，未達 60% 門檻
    df = _build_df(signal_open=80.0, signal_high=88.0, signal_low=78.0, signal_close=87.0)
    result = evaluate_shadow_reversal(df, stock_code="9999")
    assert result is None


def test_no_prior_rally():
    # 沒有前段急拉（連同訊號K棒都貼在同一價位附近），22 根K棒漲幅不足 25%
    df = _build_df(
        rally_start=59.0,
        rally_end=61.0,
        signal_open=61.0,
        signal_high=61.5,
        signal_low=59.0,
        signal_close=61.2,
    )
    result = evaluate_shadow_reversal(df, stock_code="9999")
    assert result is None


def test_does_not_break_prior_low():
    # 低點只到 83，未跌破前5根低點（約 82.8）
    df = _build_df(signal_low=83.0, signal_open=88.0, signal_high=88.0, signal_close=87.5)
    result = evaluate_shadow_reversal(df, stock_code="9999")
    assert result is None


def test_insufficient_bars():
    df = _build_df(n_base=10, n_rally=5)
    result = evaluate_shadow_reversal(df, stock_code="9999")
    assert result is None


def test_close_below_ma20_fails():
    # 收盤守不住MA20：把訊號K棒壓在區間中段，即使下影線比例仍達門檻也不通過
    df = _build_df(rally_end=80.0, signal_open=79.0, signal_high=79.5, signal_low=50.0, signal_close=70.0)
    idx = len(df) - 1
    row = df.iloc[idx]
    assert row["close"] <= row["ma20"]  # 前提：確實落在「收盤未站上MA20」的情境
    result = evaluate_shadow_reversal(df, stock_code="9999")
    assert result is None


def test_gap_down_in_window_fails():
    # 往前22根K棒中插入一根向下跳空缺口，即使其餘條件都符合也不通過
    df = _build_df()
    idx_to_gap = len(df) - 10
    prev_low = df.iloc[idx_to_gap - 1]["low"]
    df.loc[df.index[idx_to_gap], ["open", "high", "low", "close"]] = [
        prev_low - 5,
        prev_low - 4,
        prev_low - 6,
        prev_low - 4.5,
    ]
    result = evaluate_shadow_reversal(df, stock_code="9999")
    assert result is None


def test_benchmark_regime_filter():
    df = _build_df()
    signal_date = df.index[-1]

    bench_down = pd.DataFrame({"close": [100.0], "ma20": [110.0]}, index=[signal_date])
    result_down = evaluate_shadow_reversal(df, stock_code="9999", benchmark_df=bench_down)
    assert result_down is None

    bench_up = pd.DataFrame({"close": [120.0], "ma20": [110.0]}, index=[signal_date])
    result_up = evaluate_shadow_reversal(df, stock_code="9999", benchmark_df=bench_up)
    assert result_up is not None
    assert result_up.market_trend_ok is True

    # 未提供大盤資料時，視為「不檢查」，不應阻擋原本會通過的訊號
    result_no_benchmark = evaluate_shadow_reversal(df, stock_code="9999", benchmark_df=None)
    assert result_no_benchmark is not None
    assert result_no_benchmark.market_trend_ok is None


def test_random_walk_no_signal():
    rng = np.random.default_rng(42)
    base = 100 + np.cumsum(rng.normal(0, 0.3, 130))
    rows = [
        {"open": c, "high": c + 0.2, "low": c - 0.2, "close": c + 0.05, "volume": 4_000_000.0}
        for c in base
    ]
    idx = pd.bdate_range("2024-01-02", periods=len(rows))
    df = add_moving_averages(pd.DataFrame(rows, index=idx))
    result = evaluate_shadow_reversal(df, stock_code="9999")
    assert result is None
