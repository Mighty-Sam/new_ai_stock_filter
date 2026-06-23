"""trading_calendar 單元測試。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.data.trading_calendar import get_trading_days, offset_trading_days


def _bench(start: date, n: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame({"close": range(n)}, index=idx)


def test_get_trading_days_sorted():
    df = _bench(date(2024, 1, 2), 10)
    days = get_trading_days(df)
    assert len(days) == 10
    assert days[0] < days[-1]


def test_offset_back_20():
    df = _bench(date(2024, 1, 2), 30)
    ref = df.index[-1].date()
    signal = offset_trading_days(ref, -20, df)
    assert signal == df.index[-21].date()


def test_offset_zero_is_latest_on_or_before_ref():
    df = _bench(date(2024, 1, 2), 10)
    ref = df.index[5].date()
    assert offset_trading_days(ref, 0, df) == ref


def test_offset_insufficient_history_returns_none():
    df = _bench(date(2024, 1, 2), 10)
    ref = df.index[-1].date()
    assert offset_trading_days(ref, -20, df) is None


def test_empty_benchmark_returns_none():
    assert offset_trading_days(date(2024, 1, 2), -5, pd.DataFrame()) is None
