"""trade_simulator 單元測試。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.backtest.trade_simulator import (
    MAX_HOLD_DAYS,
    simulate_trade,
    simulate_trades,
)


def _make_ohlcv(start: date, n: int, base: float = 100.0) -> pd.DataFrame:
    rows = []
    for i in range(n):
        price = base + i * 0.5
        rows.append(
            {
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.2,
                "volume": 1_000_000,
            }
        )
    idx = pd.date_range(start, periods=n, freq="B")
    df = pd.DataFrame(rows[: len(idx)], index=idx)
    return df


def test_take_profit_exit():
    start = date(2024, 1, 2)
    n = 30
    rows = []
    for i in range(n):
        price = 100.0 + i * 2.0
        rows.append(
            {
                "open": price,
                "high": price + 5.0,
                "low": price - 0.5,
                "close": price + 1.0,
                "volume": 1_000_000,
            }
        )
    idx = pd.date_range(start, periods=n, freq="B")
    stock = pd.DataFrame(rows, index=idx)
    bench = _make_ohlcv(start, n, base=50.0)
    signal = stock.index[5].date()

    trade = simulate_trade("2330", stock, bench, signal)
    assert trade is not None
    assert trade.entry_date > signal
    assert trade.exit_reason == "take_profit"
    assert trade.return_pct == pytest.approx(30.0, abs=0.1)
    assert trade.alpha_pct == pytest.approx(trade.return_pct - trade.benchmark_return_pct, abs=0.02)


def test_stop_loss_exit():
    start = date(2024, 1, 2)
    n = 30
    rows = []
    for i in range(n):
        price = 100.0 - i * 2.0
        rows.append(
            {
                "open": price,
                "high": price + 0.5,
                "low": price - 2.0,
                "close": price - 0.5,
                "volume": 1_000_000,
            }
        )
    idx = pd.date_range(start, periods=n, freq="B")
    stock = pd.DataFrame(rows, index=idx)
    bench = _make_ohlcv(start, n, base=50.0)
    signal = stock.index[5].date()

    trade = simulate_trade("2330", stock, bench, signal)
    assert trade is not None
    assert trade.exit_reason == "stop"
    assert trade.return_pct == pytest.approx(-10.0, abs=0.1)


def test_simulate_trades_returns_single_trade():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 50, base=80.0)
    bench = _make_ohlcv(start, 50, base=40.0)
    signal = stock.index[5].date()

    trades = simulate_trades("2330", stock, bench, signal)
    assert len(trades) == 1


def test_timeout_at_max_hold_days():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 60, base=100.0)
    bench = _make_ohlcv(start, 60, base=50.0)
    signal = stock.index[5].date()

    trade = simulate_trade("2330", stock, bench, signal)
    assert trade is not None
    assert trade.exit_reason == "timeout"
    assert trade.hold_days == MAX_HOLD_DAYS


def test_insufficient_data_returns_none():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 5, base=100.0)
    bench = _make_ohlcv(start, 5, base=50.0)
    signal = stock.index[0].date()

    assert simulate_trade("2330", stock, bench, signal) is None
