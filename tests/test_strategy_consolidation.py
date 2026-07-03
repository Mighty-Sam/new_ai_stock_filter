"""縮幅回踩 A 級第二通道單元測試。"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from src.indicators.moving_average import add_moving_averages
from src.screener.strategy_consolidation import (
    is_gap_up_small_body,
    is_lower_shadow_candle,
    passes_consolidation_strategy,
    passes_entry_gap_projection,
    passes_entry_shadow_retest,
    projects_ma5_above_ma10_on_flat,
)


def _make_consolidation_pass_df(n: int = 130) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = [100.0 + i * 0.2 for i in range(n)]
    df = pd.DataFrame(
        {
            "open": close,
            "high": [c + 1.0 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [800_000] * n,
        },
        index=idx,
    )
    df.iloc[-50:, df.columns.get_loc("low")] = 80.0
    df.iloc[-50:, df.columns.get_loc("high")] = 105.0

    tight = close[-1]
    for i in range(-10, 0):
        df.iloc[i, df.columns.get_loc("low")] = tight - 0.5
        df.iloc[i, df.columns.get_loc("high")] = tight + 0.5
        df.iloc[i, df.columns.get_loc("close")] = tight
        df.iloc[i, df.columns.get_loc("open")] = tight

    df = add_moving_averages(df)
    last = len(df) - 1
    ma5 = float(df.iloc[last]["ma5"])
    df.iloc[last, df.columns.get_loc("open")] = ma5 + 1.0
    df.iloc[last, df.columns.get_loc("close")] = ma5 + 0.6
    df.iloc[last, df.columns.get_loc("low")] = ma5
    df.iloc[last, df.columns.get_loc("high")] = ma5 + 1.2
    df = add_moving_averages(df)
    df.iloc[last - 10, df.columns.get_loc("ma20")] = float(df.iloc[last]["ma20"]) - 2.0
    return df


def test_is_lower_shadow_candle():
    row = pd.Series({"open": 103.0, "close": 102.0, "low": 100.0, "high": 103.5})
    assert is_lower_shadow_candle(row) is True
    flat = pd.Series({"open": 100.0, "close": 100.0, "low": 100.0, "high": 101.0})
    assert is_lower_shadow_candle(flat) is False


def test_is_gap_up_small_body():
    prev = pd.Series({"close": 100.0})
    row = pd.Series({"open": 102.1, "close": 102.12, "low": 102.05, "high": 102.2})
    assert is_gap_up_small_body(row, prev) is True
    big_body = pd.Series({"open": 102.5, "close": 104.0, "low": 102.0, "high": 104.5})
    assert is_gap_up_small_body(big_body, prev) is False


def test_projects_ma5_above_ma10_on_flat():
    idx = pd.date_range("2024-01-02", periods=20, freq="B")
    close = [100 + i * 0.5 for i in range(20)]
    df = pd.DataFrame(
        {
            "open": close,
            "high": [c + 0.5 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [500_000] * 20,
        },
        index=idx,
    )
    df = add_moving_averages(df)
    last = len(df) - 1
    df.iloc[last, df.columns.get_loc("ma5")] = 109.0
    df.iloc[last, df.columns.get_loc("ma10")] = 108.5
    assert projects_ma5_above_ma10_on_flat(df) is True


def test_passes_consolidation_strategy_shadow_path():
    df = _make_consolidation_pass_df()
    assert passes_entry_shadow_retest(df.iloc[-1]) is True
    assert passes_consolidation_strategy(df) is True


def test_passes_consolidation_rejects_wide_amplitude():
    df = _make_consolidation_pass_df()
    last = len(df) - 1
    df.iloc[last - 9 : last + 1, df.columns.get_loc("low")] = 80.0
    df.iloc[last - 9 : last + 1, df.columns.get_loc("high")] = 120.0
    assert passes_consolidation_strategy(df) is False


@patch("src.screener.strategy_consolidation.projects_ma5_above_ma10_on_flat", return_value=True)
def test_passes_entry_gap_projection(mock_proj):
    idx = pd.date_range("2024-01-02", periods=130, freq="B")
    close = [100.0 + i * 0.05 for i in range(130)]
    df = pd.DataFrame(
        {
            "open": close,
            "high": [c + 0.3 for c in close],
            "low": [c - 0.3 for c in close],
            "close": close,
            "volume": [800_000] * 130,
        },
        index=idx,
    )
    df.iloc[-50:, df.columns.get_loc("low")] = 80.0
    df.iloc[-50:, df.columns.get_loc("high")] = 105.0
    tight = close[-1]
    for i in range(-10, 0):
        df.iloc[i, df.columns.get_loc("low")] = tight - 0.3
        df.iloc[i, df.columns.get_loc("high")] = tight + 0.3
        df.iloc[i, df.columns.get_loc("close")] = tight
        df.iloc[i, df.columns.get_loc("open")] = tight
    df = add_moving_averages(df)
    last = len(df) - 1
    prev_close = float(df.iloc[last - 1]["close"])
    df.iloc[last, df.columns.get_loc("open")] = prev_close * 1.020
    df.iloc[last, df.columns.get_loc("close")] = prev_close * 1.0201
    df.iloc[last, df.columns.get_loc("low")] = prev_close * 1.019
    df.iloc[last, df.columns.get_loc("high")] = prev_close * 1.021
    df.iloc[last, df.columns.get_loc("ma5")] = float(df.iloc[last]["ma10"]) - 0.1
    df.iloc[last - 10, df.columns.get_loc("ma20")] = float(df.iloc[last]["ma20"]) - 2.0
    assert passes_entry_gap_projection(df, last) is True
    mock_proj.assert_called_once()
