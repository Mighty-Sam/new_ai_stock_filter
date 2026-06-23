"""ForwardTracker 單元測試。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.backtest.storage import get_all_outcomes, init_db, insert_signal
from src.backtest.tracker import ForwardTracker, SettledTrade


def _make_ohlcv(start: date, n: int, base: float = 100.0) -> pd.DataFrame:
    rows = []
    for i in range(n):
        price = base - i * 2.0
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
    return pd.DataFrame(rows, index=idx)


def test_settle_matured_trades_returns_settled_list(tmp_path: Path):
    db_path = tmp_path / "test.db"
    signal_date = date(2024, 1, 10)
    scan_date = date(2024, 1, 10)
    insert_signal("2330", signal_date, scan_date, db_path=db_path)

    start = date(2024, 1, 2)
    stock_df = _make_ohlcv(start, 40, base=100.0)
    bench_df = _make_ohlcv(start, 40, base=50.0)

    tracker = ForwardTracker(db_path=db_path)
    with patch.object(tracker, "_fetch_stock_df", return_value=stock_df):
        tracker._benchmark_df = bench_df
        settled = tracker.settle_matured_trades(as_of=date(2024, 3, 1))

    assert len(settled) == 1
    assert isinstance(settled[0], SettledTrade)
    assert settled[0].stock_code == "2330"
    assert settled[0].exit_reason == "stop"
    assert settled[0].return_pct == pytest.approx(-10.0, abs=0.2)

    rows = get_all_outcomes(db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["exit_reason"] == "stop"


def test_settle_skips_future_exit(tmp_path: Path):
    db_path = tmp_path / "test.db"
    signal_date = date(2024, 1, 10)
    insert_signal("2330", signal_date, signal_date, db_path=db_path)

    start = date(2024, 1, 2)
    stock_df = _make_ohlcv(start, 40, base=100.0)
    bench_df = _make_ohlcv(start, 40, base=50.0)

    tracker = ForwardTracker(db_path=db_path)
    with patch.object(tracker, "_fetch_stock_df", return_value=stock_df):
        tracker._benchmark_df = bench_df
        settled = tracker.settle_matured_trades(as_of=date(2024, 1, 11))

    assert settled == []


def test_init_db_migrates_exit_reason_column(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            scan_date TEXT NOT NULL,
            entry_date TEXT,
            entry_price REAL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(stock_code, signal_date)
        );
        CREATE TABLE outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            hold_days INTEGER NOT NULL,
            exit_date TEXT NOT NULL,
            exit_price REAL NOT NULL,
            return_pct REAL NOT NULL,
            benchmark_return_pct REAL NOT NULL,
            alpha_pct REAL NOT NULL,
            is_win INTEGER NOT NULL,
            beat_benchmark INTEGER NOT NULL,
            settled_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(signal_id, hold_days)
        );
        """
    )
    conn.close()

    init_db(db_path)
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(outcomes)").fetchall()}
    conn.close()
    assert "exit_reason" in columns


def test_get_maturity_cohort_from_db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    start = date(2024, 1, 2)
    stock_df = _make_ohlcv(start, 40, base=100.0)
    bench_df = _make_ohlcv(start, 40, base=50.0)

    from src.data.trading_calendar import offset_trading_days

    as_of = bench_df.index[-1].date()
    signal_date = offset_trading_days(as_of, -20, bench_df)
    assert signal_date is not None
    insert_signal("2330", signal_date, signal_date, db_path=db_path)

    tracker = ForwardTracker(db_path=db_path)
    tracker._benchmark_df = bench_df

    with patch.object(tracker, "_fetch_stock_df", return_value=stock_df):
        tracker.settle_matured_trades(as_of=as_of)
        cohort = tracker.get_maturity_cohort(as_of)

    assert cohort.signal_date == signal_date
    assert cohort.has_trades
    assert cohort.trades[0].stock_code == "2330"


def test_get_maturity_cohort_warmup(tmp_path: Path):
    db_path = tmp_path / "test.db"
    start = date(2024, 1, 2)
    bench_df = _make_ohlcv(start, 10, base=50.0)
    tracker = ForwardTracker(db_path=db_path)
    tracker._benchmark_df = bench_df

    with patch.object(tracker, "settle_matured_trades", return_value=[]):
        cohort = tracker.get_maturity_cohort(bench_df.index[-1].date())

    assert cohort.is_warmup
    assert not cohort.has_trades
