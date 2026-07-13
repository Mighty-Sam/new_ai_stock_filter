"""N漲W底假跌破選股單元測試。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from src.screener.w_bottom import (
    W_BOTTOM_MIN_VOLUME,
    evaluate_w_bottom,
    scan_w_bottom,
    weekly_ma20_trending_up,
)


def _build_df(
    rally_low: float = 90.0,
    rally_high: float = 120.0,
    leg1_low: float = 100.0,
    rebound_high: float = 110.0,
    today_low: float = 98.0,
    today_close: float = 101.0,
    today_volume: float = 1_500_000.0,
    n_base: int = 140,
    declining_base: bool = False,
) -> pd.DataFrame:
    """建立「基期上漲 + 尾段急漲/回測 W 底」的合成 K 線資料。"""
    tail_len = 50
    idx = pd.bdate_range("2024-01-02", periods=n_base + tail_len)

    base_start, base_end = (300.0, 90.0) if declining_base else (50.0, 90.0)
    base_close = np.linspace(base_start, base_end, n_base)

    # 拉回/反彈兩段刻意留 3.0 的緩衝，避免與 leg1_low 端點重合導致 argmin 誤判成鄰近那根 K 棒
    tail_close = np.zeros(tail_len)
    tail_close[0:15] = np.linspace(base_close[-1], rally_high, 15)
    tail_close[15:25] = np.linspace(rally_high, leg1_low + 3.0, 10)
    tail_close[25:35] = np.linspace(leg1_low + 3.0, rebound_high, 10)
    tail_close[35:50] = np.linspace(rebound_high, today_close, 15)

    close = np.concatenate([base_close, tail_close])
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = close * 1.01
    low = close * 0.99

    leg1_pos = n_base + 24
    today_pos = len(close) - 1

    low[leg1_pos] = leg1_low
    close[leg1_pos] = leg1_low + 0.5
    high[leg1_pos] = max(high[leg1_pos], leg1_low + 1.0)

    low[today_pos] = today_low
    close[today_pos] = today_close
    high[today_pos] = max(today_close, today_low) + 1.0
    open_[today_pos] = (today_low + today_close) / 2

    volume = np.full(len(close), 600_000.0)
    volume[today_pos] = today_volume

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_evaluate_w_bottom_pass():
    df = _build_df()
    result = evaluate_w_bottom(df, stock_code="9999")
    assert result is not None
    assert result.stock_code == "9999"
    assert 20.0 <= result.rally_pct <= 45.0
    assert result.leg2_low < result.leg1_low
    assert result.close >= result.leg1_low
    assert result.pierce_pct >= 2.0
    assert result.stop_loss_price == result.leg2_low
    assert result.take_profit_price >= result.peak_high * 0.9


def test_evaluate_w_bottom_rally_too_small():
    df = _build_df(rally_high=100.0)  # (100-90)/90 ≈ 11% < 20%
    assert evaluate_w_bottom(df) is None


def test_evaluate_w_bottom_rally_above_tightened_max():
    # (128-90)/90 ≈ 42% under the old 50% cap but now excluded by the tightened 45% cap
    df = _build_df(rally_high=133.0, leg1_low=110.0, rebound_high=120.0, today_low=107.0, today_close=112.0)
    assert evaluate_w_bottom(df) is None


def test_evaluate_w_bottom_pierce_too_shallow():
    # 跌破幅度僅 1%（< 新門檻 2%）
    df = _build_df(leg1_low=100.0, today_low=99.0, today_close=101.0)
    assert evaluate_w_bottom(df) is None


def test_evaluate_w_bottom_pierce_at_threshold_passes():
    # 跌破幅度恰好 2%，應通過（>= 門檻）
    df = _build_df(leg1_low=100.0, today_low=98.0, today_close=101.0)
    result = evaluate_w_bottom(df)
    assert result is not None
    assert result.pierce_pct == 2.0
    assert result.tp_upside_pct >= 5.0


def test_evaluate_w_bottom_tp_upside_too_small():
    # 收盤已貼近 50K 次高點（121.2），停利空間 (121.2-116)/116 ≈ 4.5% < 5% → 跳過
    df = _build_df(today_close=116.0)
    assert evaluate_w_bottom(df) is None


def test_evaluate_w_bottom_rally_too_large():
    df = _build_df(rally_high=145.0, leg1_low=110.0, rebound_high=125.0, today_low=108.0, today_close=112.0)
    assert evaluate_w_bottom(df) is None


def test_evaluate_w_bottom_no_rebound_above_leg1():
    """第一腳後持續走弱、未真正反彈過第一腳低點，不成立 W 型態。"""
    n_base = 140
    idx = pd.bdate_range("2024-01-02", periods=n_base + 50)
    base_close = np.linspace(50.0, 90.0, n_base)

    tail_close = np.zeros(50)
    tail_close[0:15] = np.linspace(base_close[-1], 120.0, 15)
    tail_close[15:25] = np.linspace(120.0, 100.0, 10)
    tail_close[25:40] = np.linspace(100.0, 99.0, 15)
    tail_close[40:50] = np.linspace(99.0, 97.0, 10)

    close = np.concatenate([base_close, tail_close])
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = close * 1.005
    low = close * 0.995

    leg1_pos = n_base + 24
    today_pos = len(close) - 1
    low[leg1_pos] = 100.0
    close[leg1_pos] = 100.3
    high[leg1_pos] = 100.6

    low[today_pos] = 97.0
    close[today_pos] = 100.2
    high[today_pos] = 100.5
    open_[today_pos] = 98.0

    volume = np.full(len(close), 600_000.0)
    volume[today_pos] = 1_500_000.0

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    assert evaluate_w_bottom(df) is None


def test_evaluate_w_bottom_no_pierce_below_leg1():
    df = _build_df(today_low=101.0, today_close=105.0)  # 今日低點未跌破第一腳
    assert evaluate_w_bottom(df) is None


def test_evaluate_w_bottom_no_close_recovery():
    df = _build_df(today_low=95.0, today_close=97.0)  # 今日收盤未收復第一腳低點（真跌破）
    assert evaluate_w_bottom(df) is None


def test_evaluate_w_bottom_below_min_volume():
    df = _build_df(today_volume=W_BOTTOM_MIN_VOLUME - 1)
    assert evaluate_w_bottom(df) is None


def test_evaluate_w_bottom_weekly_ma_declining():
    df = _build_df(declining_base=True)
    assert evaluate_w_bottom(df) is None


def test_weekly_ma20_trending_up_true_for_uptrend():
    df = _build_df()
    assert weekly_ma20_trending_up(df, df.index[-1]) is True


def test_weekly_ma20_trending_up_false_for_downtrend():
    idx = pd.bdate_range("2024-01-02", periods=190)
    close = np.linspace(200.0, 90.0, 190)
    df = pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 600_000.0},
        index=idx,
    )
    assert weekly_ma20_trending_up(df, df.index[-1]) is False


def _weekly_levels_df(levels: list[float]) -> pd.DataFrame:
    """每週 5 個交易日、收盤固定為該週 level 的合成資料（從週一開始對齊）。"""
    closes = np.repeat(levels, 5).astype(float)
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame(
        {"open": closes, "high": closes * 1.01, "low": closes * 0.99, "close": closes, "volume": 600_000.0},
        index=idx,
    )


def test_weekly_ma20_up_4_weeks_passes():
    # 前 20 週持平後連續 4 週推升 MA20（5 個週值逐週上升）→ 通過
    levels = [100.0] * 20 + [110.0, 120.0, 130.0, 140.0]
    df = _weekly_levels_df(levels)
    assert weekly_ma20_trending_up(df, df.index[-1]) is True


def test_weekly_ma20_up_only_2_weeks_fails():
    # 僅最後 2 週推升 MA20，不足連續 4 週 → 不通過（回測驗證：放寬到 2 週會引進弱勢股）
    levels = [100.0] * 20 + [110.0, 120.0]
    df = _weekly_levels_df(levels)
    assert weekly_ma20_trending_up(df, df.index[-1]) is False


@patch("src.screener.scan_runner.get_stock_list")
@patch("src.screener.scan_runner.PriceFetcher")
def test_scan_w_bottom_smoke(mock_fetcher_cls, mock_stock_list):
    mock_stock_list.return_value = {"1111": "測試", "2222": "測試二"}
    passing = _build_df()
    short = _build_df().iloc[:30]

    fetcher = MagicMock()
    fetcher.fetch.side_effect = lambda code, end_date=None: {
        "1111": passing,
        "2222": short,
    }.get(code)
    mock_fetcher_cls.return_value = fetcher

    with patch("src.screener.scan_runner.is_trading_day", return_value=True):
        output = scan_w_bottom(max_workers=2, stock_limit=5)

    assert len(output.results) == 1
    assert output.results[0].stock_code == "1111"
    assert "1111" in output.price_data
