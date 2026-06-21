"""simulate_fixed_exit 單元測試。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.backtest.trade_simulator import simulate_fixed_exit


def _make_ohlcv(start: date, n: int, base: float = 100.0) -> pd.DataFrame:
    rows = []
    for i in range(n):
        price = base + i
        rows.append(
            {
                "open": price,
                "high": price + 2,
                "low": price - 1,
                "close": price + 0.5,
                "volume": 1_000_000,
            }
        )
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame(rows, index=idx)


def test_fixed_exit_entry_next_open_exit_close():
    stock = _make_ohlcv(date(2026, 6, 1), 20, base=100.0)
    bench = _make_ohlcv(date(2026, 6, 1), 20, base=50.0)

    # signal 6/1 (Mon) -> entry 6/2 open=101, exit 6/18 close
    trade = simulate_fixed_exit("2330", stock, bench, date(2026, 6, 1), date(2026, 6, 18))
    assert trade is not None
    assert trade.entry_date == date(2026, 6, 2)
    assert trade.entry_price == 101.0
    assert trade.exit_date == date(2026, 6, 18)
    assert trade.exit_reason == "fixed_exit"
    expected_return = (trade.exit_price - trade.entry_price) / trade.entry_price * 100
    assert trade.return_pct == round(expected_return, 2)


def test_fixed_exit_exit_before_entry_returns_none():
    stock = _make_ohlcv(date(2026, 6, 1), 5)
    bench = _make_ohlcv(date(2026, 6, 1), 5)
    assert simulate_fixed_exit("2330", stock, bench, date(2026, 6, 5), date(2026, 6, 3)) is None


def test_fixed_exit_missing_exit_date_returns_none():
    stock = _make_ohlcv(date(2026, 6, 1), 5)
    bench = _make_ohlcv(date(2026, 6, 1), 5)
    assert simulate_fixed_exit("2330", stock, bench, date(2026, 6, 1), date(2026, 7, 1)) is None


def test_fixed_exit_signal_last_bar_no_entry():
    stock = _make_ohlcv(date(2026, 6, 1), 3)
    bench = _make_ohlcv(date(2026, 6, 1), 3)
    assert simulate_fixed_exit("2330", stock, bench, date(2026, 6, 5), date(2026, 6, 5)) is None
