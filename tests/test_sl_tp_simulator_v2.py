"""sl_tp_simulator_v2 單元測試。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.backtest.sl_tp_simulator_v2 import (
    SlTpConfig,
    cross_avg_price,
    cross_low_price,
    simulate_sl_tp_v2,
)
from src.screener.conditions import ScreenResult


def _make_ohlcv(start: date, n: int, base: float = 100.0, pattern: str = "flat") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="B")
    rows = []
    for i in range(len(idx)):
        price = base + (i * 2 if pattern == "rise" else 0)
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


def _signal(df: pd.DataFrame, signal_idx: int, golden_idx: int) -> ScreenResult:
    row = df.iloc[signal_idx]
    return ScreenResult(
        stock_code="2330",
        signal_date=df.index[signal_idx],
        close=float(row["close"]),
        gain_pct=20.0,
        retest_ma="ma5",
        golden_cross_date=df.index[golden_idx],
        death_cross_date=df.index[max(0, golden_idx - 5)],
        oscillation_bars=5,
        ma20=float(row["ma20"]),
        ma60=float(row["ma60"]),
        ma120=float(row["ma120"]),
        volume=float(row["volume"]),
    )


def test_pct_10_stop_and_tp25():
    df = _make_ohlcv(date(2024, 1, 2), 40, base=200.0)
    entry_idx = 6
    df.iloc[entry_idx, df.columns.get_loc("low")] = 170.0
    sig = _signal(df, 5, 3)
    cfg = SlTpConfig(stop_type="pct_10", tp_type="pct_25", max_hold_days=20)
    trade = simulate_sl_tp_v2("2330", df, sig, cfg)
    assert trade is not None
    assert trade.exit_reason == "stop"
    assert trade.return_pct == pytest.approx(-10.0, abs=0.1)


def test_tp30_on_rise():
    df = _make_ohlcv(date(2024, 1, 2), 40, pattern="rise")
    sig = _signal(df, 5, 3)
    cfg = SlTpConfig(stop_type="pct_10", tp_type="pct_30", max_hold_days=20)
    trade = simulate_sl_tp_v2("2330", df, sig, cfg)
    assert trade is not None
    assert trade.exit_reason == "take_profit"
    assert trade.return_pct == pytest.approx(30.0, abs=0.1)


def test_cross_low_reference():
    df = _make_ohlcv(date(2024, 1, 2), 20)
    df.iloc[3, df.columns.get_loc("low")] = 88.0
    df.iloc[3, df.columns.get_loc("ma5")] = 95.0
    df.iloc[3, df.columns.get_loc("ma10")] = 93.0
    assert cross_low_price(df, df.index[3]) == pytest.approx(88.0)
    assert cross_avg_price(df, df.index[3]) == pytest.approx(94.0)


def test_cross_skip_day1_delays_stop_on_entry_day():
    df = _make_ohlcv(date(2024, 1, 2), 30, base=100.0)
    entry_idx = 6
    golden_idx = 3
    df.iloc[golden_idx, df.columns.get_loc("ma5")] = 100.0
    df.iloc[golden_idx, df.columns.get_loc("ma10")] = 98.0
    df.iloc[golden_idx, df.columns.get_loc("low")] = 99.0
    cross_ref = (100.0 + 98.0) / 2
    df.iloc[entry_idx, df.columns.get_loc("low")] = cross_ref - 1
    df.iloc[entry_idx, df.columns.get_loc("high")] = 130.0

    sig = _signal(df, 5, golden_idx)
    cfg = SlTpConfig(
        stop_type="cross_skip_day1",
        tp_type="pct_30",
        max_hold_days=20,
        entry_mode="next_open",
    )
    trade = simulate_sl_tp_v2("2330", df, sig, cfg)
    assert trade is not None
    assert trade.exit_reason == "take_profit"


def test_signal_close_entry_skips_same_day():
    df = _make_ohlcv(date(2024, 1, 2), 30, base=100.0)
    sig_idx = 5
    df.iloc[sig_idx, df.columns.get_loc("low")] = 85.0
    df.iloc[sig_idx, df.columns.get_loc("high")] = 130.0

    sig = _signal(df, sig_idx, 3)
    cfg = SlTpConfig(
        stop_type="pct_10",
        tp_type="pct_25",
        max_hold_days=20,
        entry_mode="signal_close",
    )
    trade = simulate_sl_tp_v2("2330", df, sig, cfg)
    assert trade is not None
    assert trade.hold_days >= 2


def test_hold_30_timeout():
    df = _make_ohlcv(date(2024, 1, 2), 50, base=100.0)
    for col in ("open", "high", "low", "close"):
        df[col] = 100.0
    df["high"] = 100.5
    df["low"] = 99.5

    sig = _signal(df, 5, 3)
    cfg = SlTpConfig(stop_type="pct_10", tp_type="pct_25", max_hold_days=30)
    trade = simulate_sl_tp_v2("2330", df, sig, cfg)
    assert trade is not None
    assert trade.exit_reason == "timeout"
    assert trade.hold_days == 30
