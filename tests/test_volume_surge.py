"""爆量價穩選股單元測試。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from src.indicators.moving_average import add_moving_averages
from src.screener.volume_surge import (
    VOLUME_SURGE_MAX_GAIN_PCT,
    VOLUME_SURGE_MIN_RATIO,
    VOLUME_SURGE_MIN_SHARES,
    daily_gain_pct,
    evaluate_volume_surge,
    find_volume_surge_ma_touch,
    scan_volume_surge,
    volume_ratio_vs_avg,
)


def _base_df(n: int = 65, base_vol: float = 600_000) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = [50.0 + i * 0.1 for i in range(n)]
    df = pd.DataFrame(
        {
            "open": close,
            "high": [c + 0.5 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [base_vol] * n,
        },
        index=idx,
    )
    return add_moving_averages(df)


def _passing_surge_df(gain_pct: float = 1.5, surge_mult: float = 5.0) -> pd.DataFrame:
    df = _base_df()
    last = len(df) - 1
    prev_close = float(df.iloc[last - 1]["close"])
    new_close = prev_close * (1 + gain_pct / 100)
    df.iloc[last, df.columns.get_loc("close")] = new_close
    df.iloc[last, df.columns.get_loc("open")] = prev_close
    df.iloc[last, df.columns.get_loc("high")] = new_close + 0.2
    df.iloc[last, df.columns.get_loc("volume")] = 600_000 * surge_mult
    df = add_moving_averages(df)
    ma5 = float(df.iloc[last]["ma5"])
    df.iloc[last, df.columns.get_loc("low")] = ma5
    df.iloc[last, df.columns.get_loc("open")] = max(float(df.iloc[last]["open"]), ma5 + 0.05)
    df.iloc[last, df.columns.get_loc("close")] = max(float(df.iloc[last]["close"]), ma5 + 0.1)
    assert float(df.iloc[last]["close"]) > float(df.iloc[last]["ma60"])
    return df


def test_daily_gain_pct():
    row = pd.Series({"close": 101.5})
    prev = pd.Series({"close": 100.0})
    assert daily_gain_pct(row, prev) == 1.5


def test_volume_ratio_vs_avg():
    df = _base_df()
    last = len(df) - 1
    df.iloc[last, df.columns.get_loc("volume")] = 3_000_000
    ratio = volume_ratio_vs_avg(df, last, 5)
    assert ratio == 5.0


def test_evaluate_volume_surge_pass():
    df = _passing_surge_df(gain_pct=1.5, surge_mult=5.0)
    result = evaluate_volume_surge(df, stock_code="9999")
    assert result is not None
    assert result.stock_code == "9999"
    assert result.daily_gain_pct == 1.5
    assert result.vol_ratio_5d >= VOLUME_SURGE_MIN_RATIO
    assert result.vol_ratio_20d >= VOLUME_SURGE_MIN_RATIO
    assert result.touched_ma in ("ma5", "ma10", "ma20")


def test_evaluate_volume_surge_gain_boundary():
    df_pass = _passing_surge_df(gain_pct=2.99)
    assert evaluate_volume_surge(df_pass) is not None

    df_fail = _passing_surge_df(gain_pct=VOLUME_SURGE_MAX_GAIN_PCT)
    assert evaluate_volume_surge(df_fail) is None

    df_neg = _passing_surge_df(gain_pct=-1.0)
    assert evaluate_volume_surge(df_neg) is None


def test_evaluate_volume_surge_only_5d_ratio_passes():
    df = _passing_surge_df()
    last = len(df) - 1
    df.iloc[last - 5 : last, df.columns.get_loc("volume")] = 200_000
    df.iloc[last - 20 : last - 5, df.columns.get_loc("volume")] = 600_000
    df.iloc[last, df.columns.get_loc("volume")] = 1_000_000
    assert evaluate_volume_surge(df) is None


def test_evaluate_volume_surge_below_min_shares():
    df = _passing_surge_df()
    last = len(df) - 1
    df.iloc[last, df.columns.get_loc("volume")] = VOLUME_SURGE_MIN_SHARES - 1
    assert evaluate_volume_surge(df) is None


def test_evaluate_volume_surge_below_ma60():
    df = _passing_surge_df()
    last = len(df) - 1
    ma60 = float(df.iloc[last]["ma60"])
    df.iloc[last, df.columns.get_loc("close")] = ma60 - 1
    df = add_moving_averages(df)
    assert evaluate_volume_surge(df) is None


def test_evaluate_volume_surge_no_ma_touch():
    df = _passing_surge_df()
    last = len(df) - 1
    close = float(df.iloc[last]["close"])
    df.iloc[last, df.columns.get_loc("low")] = close + 5.0
    df.iloc[last, df.columns.get_loc("open")] = close + 5.0
    df.iloc[last, df.columns.get_loc("high")] = close + 6.0
    assert evaluate_volume_surge(df) is None


def test_find_volume_surge_ma_touch_allows_slight_pierce():
    df = _passing_surge_df()
    last = len(df) - 1
    ma5 = float(df.iloc[last]["ma5"])
    df.iloc[last, df.columns.get_loc("low")] = ma5 * 0.995
    df.iloc[last, df.columns.get_loc("open")] = ma5 + 0.2
    df.iloc[last, df.columns.get_loc("close")] = ma5 + 0.5
    df.iloc[last, df.columns.get_loc("high")] = ma5 + 0.8
    assert find_volume_surge_ma_touch(df.iloc[last]) == "ma5"
    assert evaluate_volume_surge(df) is not None


@patch("src.screener.scan_runner.get_stock_list")
@patch("src.screener.scan_runner.PriceFetcher")
def test_scan_volume_surge_smoke(mock_fetcher_cls, mock_stock_list):
    mock_stock_list.return_value = {"1111": "測試", "2222": "測試二"}
    passing = _passing_surge_df()
    short = _base_df(n=30)

    fetcher = MagicMock()
    fetcher.fetch.side_effect = lambda code, end_date=None: {
        "1111": passing,
        "2222": short,
    }.get(code)
    mock_fetcher_cls.return_value = fetcher

    with patch("src.screener.scan_runner.is_trading_day", return_value=True):
        output = scan_volume_surge(max_workers=2, stock_limit=5)

    assert len(output.results) == 1
    assert output.results[0].stock_code == "1111"
    assert "1111" in output.price_data
