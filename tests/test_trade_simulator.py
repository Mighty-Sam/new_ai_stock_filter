"""trade_simulator 單元測試。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.backtest.trade_simulator import simulate_trade, simulate_trades


def _make_ohlcv(start: date, n: int, base: float = 100.0) -> pd.DataFrame:
    rows = []
    for i in range(n):
        d = start + timedelta(days=i)
        # 跳過週末簡化：只用連續日曆日當交易日
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


def test_simulate_trade_10_days():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 40, base=100.0)
    bench = _make_ohlcv(start, 40, base=50.0)
    signal = stock.index[10].date()

    trade = simulate_trade("2330", stock, bench, signal, hold_days=10)
    assert trade is not None
    assert trade.entry_date > signal
    assert trade.hold_days == 10
    assert trade.return_pct != 0
    assert trade.benchmark_return_pct != 0
    assert trade.alpha_pct == pytest.approx(trade.return_pct - trade.benchmark_return_pct, abs=0.02)


def test_simulate_trades_both_periods():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 50, base=80.0)
    bench = _make_ohlcv(start, 50, base=40.0)
    signal = stock.index[5].date()

    trades = simulate_trades("2330", stock, bench, signal)
    assert len(trades) == 2
    holds = {t.hold_days for t in trades}
    assert holds == {10, 20}


def test_insufficient_data_returns_none():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 5, base=100.0)
    bench = _make_ohlcv(start, 5, base=50.0)
    signal = stock.index[0].date()

    assert simulate_trade("2330", stock, bench, signal, hold_days=10) is None
