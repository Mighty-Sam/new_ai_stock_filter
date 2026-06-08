"""sl_tp_simulator 單元測試。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.backtest.sl_tp_simulator import (
    cross_ref_price,
    simulate_all_combos,
    simulate_sl_tp_trade,
)
from src.screener.conditions import ScreenResult


def _make_ohlcv(
    start: date,
    n: int,
    *,
    base: float = 100.0,
    daily_pattern: str = "flat",
) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="B")
    rows = []
    for i in range(len(idx)):
        price = base
        if daily_pattern == "rise":
            price = base + i * 2
        elif daily_pattern == "fall":
            price = base - i * 2

        rows.append(
            {
                "open": price,
                "high": price + 3,
                "low": price - 3,
                "close": price + 0.5,
                "volume": 1_000_000,
                "ma5": price,
                "ma10": price - 1,
                "ma20": price - 2,
                "ma60": price - 5,
                "ma120": price - 8,
            }
        )
    return pd.DataFrame(rows, index=idx)


def _signal(
    stock_df: pd.DataFrame,
    signal_idx: int,
    golden_idx: int,
) -> ScreenResult:
    row = stock_df.iloc[signal_idx]
    return ScreenResult(
        stock_code="2330",
        signal_date=stock_df.index[signal_idx],
        close=float(row["close"]),
        gain_pct=15.0,
        retest_ma="ma5",
        golden_cross_date=stock_df.index[golden_idx],
        death_cross_date=stock_df.index[max(0, golden_idx - 5)],
        oscillation_bars=5,
        ma20=float(row["ma20"]),
        ma60=float(row["ma60"]),
        ma120=float(row["ma120"]),
        volume=float(row["volume"]),
    )


def test_take_profit_on_rising_series():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 30, base=100.0, daily_pattern="rise")
    signal = _signal(stock, signal_idx=5, golden_idx=3)

    trade = simulate_sl_tp_trade("2330", stock, signal, "pct_5", "pct_10")
    assert trade is not None
    assert trade.exit_reason == "take_profit"
    assert trade.return_pct == pytest.approx(10.0, abs=0.1)


def test_stop_on_falling_series():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 30, base=200.0, daily_pattern="fall")
    signal = _signal(stock, signal_idx=5, golden_idx=3)

    trade = simulate_sl_tp_trade("2330", stock, signal, "pct_5", "pct_30")
    assert trade is not None
    assert trade.exit_reason == "stop"
    assert trade.return_pct == pytest.approx(-5.0, abs=0.1)


def test_same_day_stop_before_take_profit():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 30, base=100.0, daily_pattern="flat")
    entry_idx = 6
    stock.iloc[entry_idx, stock.columns.get_loc("low")] = 94.0
    stock.iloc[entry_idx, stock.columns.get_loc("high")] = 115.0

    signal = _signal(stock, signal_idx=5, golden_idx=3)
    trade = simulate_sl_tp_trade("2330", stock, signal, "pct_5", "pct_10")
    assert trade is not None
    assert trade.exit_reason == "stop"
    assert trade.exit_price == pytest.approx(95.0, abs=0.01)


def test_timeout_on_day_20():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 40, base=100.0, daily_pattern="flat")
    for col in ("open", "high", "low", "close"):
        stock[col] = 100.0
    stock["high"] = 100.5
    stock["low"] = 99.5

    signal = _signal(stock, signal_idx=5, golden_idx=3)
    trade = simulate_sl_tp_trade("2330", stock, signal, "pct_5", "pct_10")
    assert trade is not None
    assert trade.exit_reason == "timeout"
    assert trade.hold_days == 20


def test_cross_ref_price_from_golden_cross_date():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 20, base=100.0)
    stock.iloc[3, stock.columns.get_loc("ma5")] = 40.0
    stock.iloc[3, stock.columns.get_loc("ma10")] = 38.0

    ref = cross_ref_price(stock, stock.index[3])
    assert ref == pytest.approx(39.0, abs=0.01)


def test_simulate_all_combos_returns_nine():
    start = date(2024, 1, 2)
    stock = _make_ohlcv(start, 30, base=100.0, daily_pattern="flat")
    signal = _signal(stock, signal_idx=5, golden_idx=3)
    trades = simulate_all_combos("2330", stock, signal)
    assert len(trades) == 9
