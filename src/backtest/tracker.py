"""前瞻信號追蹤與結算。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

import pandas as pd

from src.backtest.stats import BacktestSummary, aggregate_trades
from src.backtest.storage import (
    OPTIMIZED_DB_PATH,
    get_all_outcomes,
    get_pending_signals,
    insert_outcome,
    insert_signal,
    mark_signal_status,
    update_signal_entry,
)
from src.backtest.trade_simulator import ExitReason, TradeResult, simulate_trades
from src.data.benchmark import fetch_benchmark
from src.data.price_fetcher import PriceFetcher
from src.screener.conditions import ScreenResult

logger = logging.getLogger(__name__)

EXIT_REASON_LABELS = {
    "stop": "停損",
    "take_profit": "停利",
    "timeout": "到期",
    "fixed_exit": "固定出場",
}


@dataclass
class SettledTrade:
    stock_code: str
    signal_date: date
    return_pct: float
    exit_reason: ExitReason
    exit_date: date
    hold_days: int


class ForwardTracker:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or OPTIMIZED_DB_PATH
        self.fetcher = PriceFetcher(delay=0.05)
        self._benchmark_df = None

    @property
    def benchmark_df(self):
        if self._benchmark_df is None:
            try:
                self._benchmark_df = fetch_benchmark()
            except RuntimeError as exc:
                logger.error("無法載入 0050 基準: %s", exc)
                raise
        return self._benchmark_df

    def record_signals(self, results: List[ScreenResult], scan_date: date) -> int:
        count = 0
        for r in results:
            sig_date = pd.Timestamp(r.signal_date).date()
            sid = insert_signal(r.stock_code, sig_date, scan_date, db_path=self.db_path)
            if sid is not None:
                count += 1
        logger.info("前瞻追蹤：新增/確認 %d 筆信號", count)
        return count

    def _fetch_stock_df(self, stock_code: str) -> Optional[pd.DataFrame]:
        return self.fetcher.fetch(stock_code, days=80)

    def settle_matured_trades(self, as_of: Optional[date] = None) -> List[SettledTrade]:
        pending = get_pending_signals(db_path=self.db_path)
        settled: List[SettledTrade] = []
        today = as_of or date.today()

        for row in pending:
            signal_id = int(row["id"])
            stock_code = row["stock_code"]
            signal_date = date.fromisoformat(row["signal_date"])

            df = self._fetch_stock_df(stock_code)
            if df is None or df.empty:
                continue

            trades = simulate_trades(stock_code, df, self.benchmark_df, signal_date)
            if not trades:
                continue

            trade = trades[0]
            if row["entry_price"] is None:
                update_signal_entry(
                    signal_id,
                    trade.entry_date,
                    trade.entry_price,
                    db_path=self.db_path,
                )

            if trade.exit_date > today:
                continue

            insert_outcome(
                signal_id=signal_id,
                hold_days=trade.hold_days,
                exit_date=trade.exit_date,
                exit_price=trade.exit_price,
                return_pct=trade.return_pct,
                benchmark_return_pct=trade.benchmark_return_pct,
                alpha_pct=trade.alpha_pct,
                is_win=trade.is_win,
                beat_benchmark=trade.beat_benchmark,
                exit_reason=trade.exit_reason,
                db_path=self.db_path,
            )
            mark_signal_status(signal_id, "settled", db_path=self.db_path)
            settled.append(
                SettledTrade(
                    stock_code=stock_code,
                    signal_date=signal_date,
                    return_pct=trade.return_pct,
                    exit_reason=trade.exit_reason,
                    exit_date=trade.exit_date,
                    hold_days=trade.hold_days,
                )
            )
            logger.info("結算信號 %s %s（%s）", stock_code, signal_date, trade.exit_reason)

        if settled:
            logger.info("前瞻追蹤：結算 %d 筆信號", len(settled))
        return settled

    def get_stats(self) -> BacktestSummary:
        rows = get_all_outcomes(db_path=self.db_path)
        trades: List[TradeResult] = []
        for row in rows:
            if row["entry_date"] is None or row["entry_price"] is None:
                continue
            trades.append(
                TradeResult(
                    stock_code=row["stock_code"],
                    signal_date=date.fromisoformat(row["signal_date"]),
                    entry_date=date.fromisoformat(row["entry_date"]),
                    entry_price=float(row["entry_price"]),
                    exit_date=date.fromisoformat(row["exit_date"]),
                    exit_price=float(row["exit_price"]),
                    hold_days=int(row["hold_days"]),
                    return_pct=float(row["return_pct"]),
                    benchmark_return_pct=float(row["benchmark_return_pct"]),
                    alpha_pct=float(row["alpha_pct"]),
                    is_win=bool(row["is_win"]),
                    beat_benchmark=bool(row["beat_benchmark"]),
                    exit_reason=row["exit_reason"] or "timeout",
                )
            )
        return aggregate_trades(trades, source="forward")

    def count_pending(self) -> int:
        return len(get_pending_signals(db_path=self.db_path))
