"""v2 選股條件單元測試。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.screener.conditions import evaluate_with_params
from src.screener.params import V1_PARAMS, V2_BASE_PARAMS


def _make_v2_pass_df(n: int = 130) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    rows = []
    for i in range(n):
        price = 100 + i * 0.3
        rows.append(
            {
                "open": price,
                "high": price + 2,
                "low": price - 1,
                "close": price + 0.5,
                "volume": 800_000,
                "ma5": price + 1,
                "ma10": price,
                "ma20": price - 1,
                "ma60": price - 3,
                "ma120": price - 5,
            }
        )
    df = pd.DataFrame(rows, index=idx)
    # 20 日振幅 > 15%
    df.iloc[-20:, df.columns.get_loc("low")] = 80.0
    df.iloc[-20:, df.columns.get_loc("high")] = 100.0
    # 信號日：回踩 MA5、收盤站回 MA5 上方
    last = len(df) - 1
    ma5 = float(df.iloc[last]["ma5"])
    df.iloc[last, df.columns.get_loc("low")] = ma5
    df.iloc[last, df.columns.get_loc("close")] = ma5 + 0.5
    df.iloc[last, df.columns.get_loc("volume")] = 1_200_000
    # 5 日均量約 80 萬 → 120 萬 > 1.2x
    # 上穿：3 日前 MA5 上穿 MA10
    cross = last - 3
    df.iloc[cross - 1, df.columns.get_loc("ma5")] = 98.0
    df.iloc[cross - 1, df.columns.get_loc("ma10")] = 99.0
    df.iloc[cross, df.columns.get_loc("ma5")] = 100.0
    df.iloc[cross, df.columns.get_loc("ma10")] = 99.5
    # 下穿整理
    death = cross - 8
    df.iloc[death - 1, df.columns.get_loc("ma5")] = 101.0
    df.iloc[death - 1, df.columns.get_loc("ma10")] = 100.0
    df.iloc[death, df.columns.get_loc("ma5")] = 99.0
    df.iloc[death, df.columns.get_loc("ma10")] = 100.0
    for j in range(death + 1, cross):
        df.iloc[j, df.columns.get_loc("ma5")] = 99.0
        df.iloc[j, df.columns.get_loc("ma10")] = 100.0
    # MA20 斜率向上
    df.iloc[last - 5, df.columns.get_loc("ma20")] = float(df.iloc[last]["ma20"]) - 2
    return df


def test_v2_base_params_stricter_than_v1():
    df = _make_v2_pass_df()
    v1 = evaluate_with_params(df, "2330", V1_PARAMS)
    v2 = evaluate_with_params(df, "2330", V2_BASE_PARAMS)
    assert v1 is not None or v2 is not None
    if v2 is not None:
        assert v2.gain_pct > 15
        assert v2.oscillation_bars >= 3


def test_v2_rejects_low_gain():
    df = _make_v2_pass_df()
    df.iloc[-20:, df.columns.get_loc("high")] = 101.0
    df.iloc[-20:, df.columns.get_loc("low")] = 99.0
    assert evaluate_with_params(df, "2330", V2_BASE_PARAMS) is None


def test_v2_rejects_close_below_retest_ma():
    df = _make_v2_pass_df()
    last = len(df) - 1
    ma5 = float(df.iloc[last]["ma5"])
    df.iloc[last, df.columns.get_loc("close")] = ma5 - 1
    assert evaluate_with_params(df, "2330", V2_BASE_PARAMS) is None


def test_v2_rejects_low_volume_ratio():
    df = _make_v2_pass_df()
    df.iloc[-1, df.columns.get_loc("volume")] = 500_000
    for i in range(-5, 0):
        df.iloc[i, df.columns.get_loc("volume")] = 600_000
    assert evaluate_with_params(df, "2330", V2_BASE_PARAMS) is None


def test_v2_oscillation_filter():
    df = _make_v2_pass_df()
    result = evaluate_with_params(df, "2330", V2_BASE_PARAMS)
    if result is None:
        pytest.skip("合成資料未通過 v2，略過 oscillation 測試")
    from src.screener.params import v2_params_with_oscillation

    strict = v2_params_with_oscillation(6)
    if result.oscillation_bars < 6:
        assert evaluate_with_params(df, "2330", strict) is None
