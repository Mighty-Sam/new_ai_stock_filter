"""回測 SQLite 儲存。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Generator, List, Optional

DB_PATH = Path("data/backtest.db")
OPTIMIZED_DB_PATH = Path("data/backtest_optimized.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
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

CREATE TABLE IF NOT EXISTS outcomes (
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
    exit_reason TEXT,
    settled_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (signal_id) REFERENCES signals(id),
    UNIQUE(signal_id, hold_days)
);
"""


def _migrate_schema(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(outcomes)").fetchall()
    }
    if "exit_reason" not in columns:
        conn.execute("ALTER TABLE outcomes ADD COLUMN exit_reason TEXT")


@contextmanager
def get_connection(db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(_SCHEMA)
        _migrate_schema(conn)


def insert_signal(
    stock_code: str,
    signal_date: date,
    scan_date: date,
    db_path: Optional[Path] = None,
) -> Optional[int]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO signals (stock_code, signal_date, scan_date, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (stock_code, signal_date.isoformat(), scan_date.isoformat()),
            )
            if cur.rowcount == 0:
                row = conn.execute(
                    "SELECT id FROM signals WHERE stock_code=? AND signal_date=?",
                    (stock_code, signal_date.isoformat()),
                ).fetchone()
                return int(row["id"]) if row else None
            return int(cur.lastrowid)
        except sqlite3.Error:
            return None


def get_pending_signals(db_path: Optional[Path] = None) -> List[sqlite3.Row]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        return list(conn.execute("SELECT * FROM signals WHERE status='pending'"))


def update_signal_entry(
    signal_id: int,
    entry_date: date,
    entry_price: float,
    db_path: Optional[Path] = None,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE signals SET entry_date=?, entry_price=? WHERE id=?",
            (entry_date.isoformat(), entry_price, signal_id),
        )


def mark_signal_status(signal_id: int, status: str, db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE signals SET status=? WHERE id=?", (status, signal_id))


def insert_outcome(
    signal_id: int,
    hold_days: int,
    exit_date: date,
    exit_price: float,
    return_pct: float,
    benchmark_return_pct: float,
    alpha_pct: float,
    is_win: bool,
    beat_benchmark: bool,
    exit_reason: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO outcomes
            (signal_id, hold_days, exit_date, exit_price, return_pct,
             benchmark_return_pct, alpha_pct, is_win, beat_benchmark, exit_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                hold_days,
                exit_date.isoformat(),
                exit_price,
                return_pct,
                benchmark_return_pct,
                alpha_pct,
                int(is_win),
                int(beat_benchmark),
                exit_reason,
            ),
        )


def get_all_outcomes(db_path: Optional[Path] = None) -> List[sqlite3.Row]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        return list(
            conn.execute(
                """
                SELECT o.*, s.stock_code, s.signal_date, s.entry_date, s.entry_price
                FROM outcomes o
                JOIN signals s ON s.id = o.signal_id
                ORDER BY o.settled_at DESC
                """
            )
        )


def get_outcomes_by_signal_date(
    signal_date: date,
    db_path: Optional[Path] = None,
) -> List[sqlite3.Row]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        return list(
            conn.execute(
                """
                SELECT o.*, s.stock_code, s.signal_date, s.entry_date, s.entry_price
                FROM outcomes o
                JOIN signals s ON s.id = o.signal_id
                WHERE s.signal_date = ?
                ORDER BY s.stock_code
                """,
                (signal_date.isoformat(),),
            )
        )


def count_pending_signals(db_path: Optional[Path] = None) -> int:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM signals WHERE status='pending'").fetchone()
        return int(row["c"]) if row else 0
