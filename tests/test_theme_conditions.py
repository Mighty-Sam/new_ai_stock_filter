"""theme_conditions 單元測試。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.data.stock_metadata import StockMetadata
from src.screener.theme_conditions import (
    ThemeScreenResult,
    evaluate_theme_candidate,
    filter_by_hot_industries,
)
from src.screener.theme_params import (
    MAX_MARKET_CAP_BILLION,
    MAX_PRICE,
    MIN_DIRECTOR_HOLDING_PCT,
    MIN_GAIN_20D_PCT,
    VOLUME_BREAKOUT_RATIO,
    VOLUME_MA_DAYS,
)


def _rising_df(n: int = 30, base: float = 50.0, vol: float = 2_000_000) -> pd.DataFrame:
    rows = []
    for i in range(n):
        price = base + i * 1.5
        rows.append(
            {
                "open": price,
                "high": price + 1.0,
                "low": price - 0.5,
                "close": price,
                "volume": vol,
            }
        )
    # 最後一天放量突破
    rows[-1]["high"] = rows[-1]["close"] + 2
    rows[-1]["volume"] = vol * 3
    idx = pd.date_range(date(2024, 1, 2), periods=n, freq="B")
    return pd.DataFrame(rows, index=idx)


def _meta(industry: str = "電子零組件業") -> StockMetadata:
    return StockMetadata(industry=industry, groups=("測試族群",))


def test_passes_all_conditions():
    df = _rising_df(n=30, base=30.0)
    # 最後一日收盤需為 20 日新高
    high_20 = float(df["high"].tail(VOLUME_MA_DAYS).max())
    df.iloc[-1, df.columns.get_loc("close")] = high_20
    df.iloc[-1, df.columns.get_loc("high")] = high_20
    df.iloc[-1, df.columns.get_loc("open")] = high_20 - 0.5

    result = evaluate_theme_candidate(
        df,
        "1234",
        market_cap_billions=MAX_MARKET_CAP_BILLION - 10,
        director_holding_pct=MIN_DIRECTOR_HOLDING_PCT + 5,
        metadata=_meta(),
    )
    assert result is not None
    assert result.close <= MAX_PRICE
    assert result.gain_20d_pct >= MIN_GAIN_20D_PCT
    assert result.volume_ratio >= VOLUME_BREAKOUT_RATIO


def test_rejects_high_price():
    df = _rising_df(base=100.0)
    result = evaluate_theme_candidate(
        df,
        "1234",
        market_cap_billions=50,
        director_holding_pct=30,
        metadata=_meta(),
    )
    assert result is None


def test_rejects_large_market_cap():
    df = _rising_df()
    result = evaluate_theme_candidate(
        df,
        "1234",
        market_cap_billions=MAX_MARKET_CAP_BILLION + 1,
        director_holding_pct=30,
        metadata=_meta(),
    )
    assert result is None


def test_rejects_low_director_holding():
    df = _rising_df()
    result = evaluate_theme_candidate(
        df,
        "1234",
        market_cap_billions=50,
        director_holding_pct=MIN_DIRECTOR_HOLDING_PCT - 1,
        metadata=_meta(),
    )
    assert result is None


def test_filter_by_hot_industries():
    candidates = [
        ThemeScreenResult(
            stock_code="1111",
            signal_date=pd.Timestamp("2024-06-01"),
            close=50,
            gain_20d_pct=20,
            volume_ratio=2.5,
            market_cap_billions=100,
            director_holding_pct=30,
            industry="半導體業",
            groups=(),
            high_20d=49,
        ),
        ThemeScreenResult(
            stock_code="2222",
            signal_date=pd.Timestamp("2024-06-01"),
            close=45,
            gain_20d_pct=18,
            volume_ratio=2.2,
            market_cap_billions=80,
            director_holding_pct=28,
            industry="半導體業",
            groups=(),
            high_20d=44,
        ),
        ThemeScreenResult(
            stock_code="3333",
            signal_date=pd.Timestamp("2024-06-01"),
            close=40,
            gain_20d_pct=16,
            volume_ratio=2.1,
            market_cap_billions=60,
            director_holding_pct=26,
            industry="營建業",
            groups=(),
            high_20d=39,
        ),
    ]
    filtered, hot = filter_by_hot_industries(candidates, top_n=1)
    assert hot == ["半導體業"]
    assert len(filtered) == 2
    assert all(r.industry == "半導體業" for r in filtered)
