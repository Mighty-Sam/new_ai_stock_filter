"""is_trading_day 單元測試。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.screener.scanner import is_trading_day


class _FakeFetcher:
    def __init__(self, latest: date):
        self.latest = latest

    def fetch(self, stock_code, days=20, end_date=None, min_rows=1, **kwargs):
        idx = pd.date_range(end=pd.Timestamp(self.latest), periods=5, freq="B")
        return pd.DataFrame(
            {
                "open": [100.0] * len(idx),
                "high": [101.0] * len(idx),
                "low": [99.0] * len(idx),
                "close": [100.0] * len(idx),
                "volume": [1_000_000.0] * len(idx),
            },
            index=idx,
        )


def test_weekend_is_not_trading_day():
    sat = date(2026, 6, 6)
    assert is_trading_day(_FakeFetcher(date(2026, 6, 5)), sat) is False


def test_monday_with_friday_bar_is_trading_day():
    """週一僅有週五 K 棒時仍為交易日（原 bug）。"""
    mon = date(2026, 6, 8)
    fri = date(2026, 6, 5)
    assert is_trading_day(_FakeFetcher(fri), mon) is True


def test_same_day_bar_is_trading_day():
    ref = date(2026, 6, 8)
    assert is_trading_day(_FakeFetcher(ref), ref) is True
