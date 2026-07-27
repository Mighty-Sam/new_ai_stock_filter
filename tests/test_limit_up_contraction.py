"""漲停量縮整理選股單元測試。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from src.screener.limit_up_contraction import (
    LIMIT_UP_MIN_GAIN_PCT,
    LIMIT_UP_MIN_VOLUME,
    evaluate_limit_up_contraction,
    scan_limit_up_contraction,
)


def _build_df(
    n_base: int = 40,
    base_rising: bool = True,
    gain_pct: float = 9.7,
    day1_vol: float = 3_000_000.0,
    day2_vol: float = 2_000_000.0,
    day3_vol: float = 1_500_000.0,
    day4_vol: float = 1_000_000.0,
    day2_bearish: bool = True,
    day3_close: float | None = None,
    day4_close: float | None = None,
) -> pd.DataFrame:
    """基期趨勢 + 尾段 4 根 K 棒（day1 漲停 → day2/3/4 量縮整理）合成資料。"""
    base_start, base_end = (50.0, 68.0) if base_rising else (60.0, 60.0)
    base = np.linspace(base_start, base_end, n_base)
    prev_close = float(base[-1])

    day1_close = round(prev_close * (1 + gain_pct / 100), 2)
    day1_high = day1_close  # 鎖漲停：收在最高
    day1_low = round(prev_close * 1.005, 2)
    day1_open = round(prev_close * 1.02, 2)

    day2_open = day1_close
    day2_close = day2_open - 2.0 if day2_bearish else day2_open + 2.0

    d3_close = day3_close if day3_close is not None else round(day1_high - 3.5, 2)
    d4_close = day4_close if day4_close is not None else round(day1_high - 4.0, 2)

    rows = []
    # 基期
    for c in base:
        rows.append({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 800_000.0})
    # day1 漲停
    rows.append({"open": day1_open, "high": day1_high, "low": day1_low, "close": day1_close, "volume": day1_vol})
    # day2 收陰線
    rows.append({"open": day2_open, "high": day1_high, "low": day1_low, "close": day2_close, "volume": day2_vol})
    # day3
    rows.append({"open": d3_close + 0.5, "high": d3_close + 0.8, "low": d3_close - 0.5, "close": d3_close, "volume": day3_vol})
    # day4（訊號日）
    rows.append({"open": d4_close + 0.5, "high": d4_close + 0.8, "low": d4_close - 0.5, "close": d4_close, "volume": day4_vol})

    idx = pd.bdate_range("2024-01-02", periods=len(rows))
    df = pd.DataFrame(rows, index=idx)
    df["ma20"] = df["close"].rolling(window=20, min_periods=20).mean()
    return df


def test_evaluate_pass():
    df = _build_df()
    result = evaluate_limit_up_contraction(df, stock_code="9999")
    assert result is not None
    assert result.stock_code == "9999"
    assert result.day1_gain_pct >= LIMIT_UP_MIN_GAIN_PCT
    assert result.day2_volume < 2.0 * result.day1_volume
    assert result.day3_volume < result.day2_volume
    assert result.day4_volume < result.day3_volume
    assert result.day1_low <= result.close <= result.day1_high
    assert result.stop_loss_price == result.day1_low
    assert result.take_profit_pct == 20.0


def test_gain_below_threshold():
    df = _build_df(gain_pct=8.0)  # < 9.5%
    assert evaluate_limit_up_contraction(df) is None


def test_ma20_not_rising():
    df = _build_df(base_rising=False)  # 基期持平 → MA20 不遞增
    assert evaluate_limit_up_contraction(df) is None


def test_day2_not_bearish():
    df = _build_df(day2_bearish=False)  # day2 收紅
    assert evaluate_limit_up_contraction(df) is None


def test_day2_volume_at_2x_rejected():
    df = _build_df(day1_vol=1_000_000.0, day2_vol=2_000_000.0)  # 恰 2×，嚴格 < 應排除
    assert evaluate_limit_up_contraction(df) is None


def test_day3_volume_not_lower():
    df = _build_df(day2_vol=1_500_000.0, day3_vol=1_500_000.0)  # day3 未低於 day2
    assert evaluate_limit_up_contraction(df) is None


def test_day4_volume_not_lower():
    df = _build_df(day3_vol=1_200_000.0, day4_vol=1_200_000.0)  # day4 未低於 day3
    assert evaluate_limit_up_contraction(df) is None


def test_day3_close_above_day1_high():
    df = _build_df()
    day1_high = float(df.iloc[-4]["high"])
    df.iloc[-2, df.columns.get_loc("close")] = day1_high + 1.0  # day3 收盤高於 day1 高
    assert evaluate_limit_up_contraction(df) is None


def test_day4_close_above_day1_high():
    df = _build_df()
    day1_high = float(df.iloc[-4]["high"])
    df.iloc[-1, df.columns.get_loc("close")] = day1_high + 1.0  # day4 收盤高於 day1 高
    assert evaluate_limit_up_contraction(df) is None


def test_close_below_day1_low():
    df = _build_df()
    day1_low = float(df.iloc[-4]["low"])
    df.iloc[-1, df.columns.get_loc("close")] = day1_low - 1.0  # day4 收盤跌破 day1 低
    assert evaluate_limit_up_contraction(df) is None


def test_day1_volume_below_floor():
    df = _build_df(day1_vol=LIMIT_UP_MIN_VOLUME - 1, day2_vol=900_000.0, day3_vol=800_000.0, day4_vol=700_000.0)
    assert evaluate_limit_up_contraction(df) is None


def test_insufficient_bars():
    df = _build_df().iloc[:20]
    assert evaluate_limit_up_contraction(df) is None


@patch("src.screener.scan_runner.get_stock_list")
@patch("src.screener.scan_runner.PriceFetcher")
def test_scan_limit_up_contraction_smoke(mock_fetcher_cls, mock_stock_list):
    mock_stock_list.return_value = {"1111": "測試", "2222": "測試二"}
    passing = _build_df()
    short = _build_df().iloc[:20]

    fetcher = MagicMock()
    fetcher.fetch.side_effect = lambda code, end_date=None: {
        "1111": passing,
        "2222": short,
    }.get(code)
    mock_fetcher_cls.return_value = fetcher

    with patch("src.screener.scan_runner.is_trading_day", return_value=True):
        output = scan_limit_up_contraction(max_workers=2, stock_limit=5)

    assert len(output.results) == 1
    assert output.results[0].stock_code == "1111"
    assert "1111" in output.price_data
