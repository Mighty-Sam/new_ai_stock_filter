"""simulate_w_bottom_trade 單元測試。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.backtest.w_bottom_simulator import simulate_w_bottom_trade


def _make_ohlcv(start: date, closes: list[float], base: float = 50.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="B")
    rows = [
        {"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1_000_000}
        for c in closes
    ]
    return pd.DataFrame(rows, index=idx)


def _flat_benchmark(start: date, n: int) -> pd.DataFrame:
    closes = [base * 1.0 for base in [50.0] * n]
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame(
        [{"open": c, "high": c, "low": c, "close": c, "volume": 1_000_000} for c in closes],
        index=idx,
    )


def test_take_profit_triggers_when_high_touches_target():
    closes = [100.0] * 3 + [101.0, 102.0, 108.0, 103.0, 103.0]
    stock = _make_ohlcv(date(2026, 6, 1), closes)
    bench = _flat_benchmark(date(2026, 6, 1), len(closes))

    trade = simulate_w_bottom_trade(
        "2330",
        stock,
        bench,
        signal_date=date(2026, 6, 1),
        take_profit_price=107.0,
        stop_loss_price=90.0,
    )
    assert trade is not None
    assert trade.exit_reason == "take_profit"
    assert trade.exit_price == 107.0


def test_stop_loss_triggers_on_close_below_target():
    closes = [100.0] * 3 + [95.0, 89.0, 86.0, 90.0]
    stock = _make_ohlcv(date(2026, 6, 1), closes)
    bench = _flat_benchmark(date(2026, 6, 1), len(closes))

    trade = simulate_w_bottom_trade(
        "2330",
        stock,
        bench,
        signal_date=date(2026, 6, 1),
        take_profit_price=150.0,
        stop_loss_price=90.0,
    )
    assert trade is not None
    assert trade.exit_reason == "stop"
    assert trade.exit_price == 89.0


def test_same_bar_conflict_favors_stop_loss():
    # 同一天高點觸及停利，但收盤已跌破停損 -> 保守先判停損
    closes = [100.0] * 3 + [95.0]
    stock = _make_ohlcv(date(2026, 6, 1), closes)
    stock.iloc[-1, stock.columns.get_loc("high")] = 120.0  # 當日高點也觸及停利價
    bench = _flat_benchmark(date(2026, 6, 1), len(closes))

    trade = simulate_w_bottom_trade(
        "2330",
        stock,
        bench,
        signal_date=date(2026, 6, 1),
        take_profit_price=110.0,
        stop_loss_price=96.0,
    )
    assert trade is not None
    assert trade.exit_reason == "stop"


def test_timeout_forces_close_exit_when_neither_triggers():
    closes = [100.0 + i * 0.1 for i in range(70)]
    stock = _make_ohlcv(date(2026, 1, 5), closes)
    bench = _flat_benchmark(date(2026, 1, 5), len(closes))

    trade = simulate_w_bottom_trade(
        "2330",
        stock,
        bench,
        signal_date=date(2026, 1, 5),
        take_profit_price=1000.0,
        stop_loss_price=1.0,
        max_hold_days=10,
    )
    assert trade is not None
    assert trade.exit_reason == "timeout"
    assert trade.hold_days == 10


def test_no_entry_when_signal_is_last_bar():
    stock = _make_ohlcv(date(2026, 6, 1), [100.0, 101.0, 102.0])
    bench = _flat_benchmark(date(2026, 6, 1), 3)
    trade = simulate_w_bottom_trade(
        "2330",
        stock,
        bench,
        signal_date=date(2026, 6, 3),
        take_profit_price=110.0,
        stop_loss_price=90.0,
    )
    assert trade is None
