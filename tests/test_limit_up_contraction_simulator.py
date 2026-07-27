"""simulate_limit_up_contraction_trade 單元測試。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.backtest.limit_up_contraction_simulator import simulate_limit_up_contraction_trade


def _make_ohlcv(start: date, closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="B")
    rows = [
        {"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1_000_000}
        for c in closes
    ]
    return pd.DataFrame(rows, index=idx)


def _flat_benchmark(start: date, n: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame(
        [{"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0, "volume": 1_000_000}] * n,
        index=idx,
    )


def test_take_profit_at_entry_plus_pct():
    # 進場價=101（signal 6/1 → entry 6/2 open），+20% = 121.2；第 4 根 high 觸及
    closes = [100.0, 101.0, 105.0, 110.0, 122.0, 115.0]
    stock = _make_ohlcv(date(2026, 6, 1), closes)
    bench = _flat_benchmark(date(2026, 6, 1), len(closes))

    trade = simulate_limit_up_contraction_trade(
        "2330", stock, bench, signal_date=date(2026, 6, 1),
        stop_loss_price=90.0, take_profit_pct=20.0,
    )
    assert trade is not None
    assert trade.entry_price == 101.0
    assert trade.exit_reason == "take_profit"
    assert trade.exit_price == round(101.0 * 1.20, 4)  # 121.2


def test_stop_loss_on_close_below_level():
    closes = [100.0, 101.0, 98.0, 88.0, 92.0]
    stock = _make_ohlcv(date(2026, 6, 1), closes)
    bench = _flat_benchmark(date(2026, 6, 1), len(closes))

    trade = simulate_limit_up_contraction_trade(
        "2330", stock, bench, signal_date=date(2026, 6, 1),
        stop_loss_price=90.0, take_profit_pct=20.0,
    )
    assert trade is not None
    assert trade.exit_reason == "stop"
    assert trade.exit_price == 88.0


def test_same_bar_conflict_favors_stop():
    # 進場 6/2 open=101；當日 high 觸及停利價，但收盤已跌破停損 → 先判停損
    closes = [100.0, 101.0, 95.0]
    stock = _make_ohlcv(date(2026, 6, 1), closes)
    stock.iloc[-1, stock.columns.get_loc("high")] = 130.0  # 也觸及 +20% 停利
    bench = _flat_benchmark(date(2026, 6, 1), len(closes))

    trade = simulate_limit_up_contraction_trade(
        "2330", stock, bench, signal_date=date(2026, 6, 1),
        stop_loss_price=96.0, take_profit_pct=20.0,
    )
    assert trade is not None
    assert trade.exit_reason == "stop"


def test_timeout_close_exit():
    closes = [100.0] + [100.0 + i * 0.1 for i in range(30)]
    stock = _make_ohlcv(date(2026, 1, 5), closes)
    bench = _flat_benchmark(date(2026, 1, 5), len(closes))

    trade = simulate_limit_up_contraction_trade(
        "2330", stock, bench, signal_date=date(2026, 1, 5),
        stop_loss_price=1.0, take_profit_pct=1000.0, max_hold_days=10,
    )
    assert trade is not None
    assert trade.exit_reason == "timeout"
    assert trade.hold_days == 10


def test_no_entry_when_signal_is_last_bar():
    stock = _make_ohlcv(date(2026, 6, 1), [100.0, 101.0, 102.0])
    bench = _flat_benchmark(date(2026, 6, 1), 3)
    trade = simulate_limit_up_contraction_trade(
        "2330", stock, bench, signal_date=date(2026, 6, 3),
        stop_loss_price=90.0, take_profit_pct=20.0,
    )
    assert trade is None
