"""前瞻信號追蹤與結算。"""

from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

import pandas as pd

from src.backtest.stats import BacktestSummary, aggregate_trades
from src.backtest.storage import (
    get_all_outcomes,
    get_pending_signals,
    insert_outcome,
    insert_signal,
    mark_signal_status,
    update_signal_entry,
)
from src.backtest.trade_simulator import TradeResult, simulate_trades
from src.data.benchmark import fetch_benchmark
from src.data.price_fetcher import PriceFetcher
from src.screener.conditions import ScreenResult

logger = logging.getLogger(__name__)


class ForwardTracker:
    def __init__(self):
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
            sid = insert_signal(r.stock_code, sig_date, scan_date)
            if sid is not None:
                count += 1
        logger.info("前瞻追蹤：新增/確認 %d 筆信號", count)
        return count

    def _fetch_stock_df(self, stock_code: str) -> Optional[pd.DataFrame]:
        return self.fetcher.fetch(stock_code, days=80)

    def settle_matured_trades(self) -> int:
        pending = get_pending_signals()
        settled_count = 0
        today = date.today()

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
                update_signal_entry(signal_id, trade.entry_date, trade.entry_price)

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
            )
            mark_signal_status(signal_id, "settled")
            settled_count += 1
            logger.info("結算信號 %s %s（%s）", stock_code, signal_date, trade.exit_reason)

        if settled_count:
            logger.info("前瞻追蹤：結算 %d 筆信號", settled_count)
        return settled_count

    def get_stats(self) -> BacktestSummary:
        rows = get_all_outcomes()
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
                )
            )
        return aggregate_trades(trades, source="forward")

    def count_pending(self) -> int:
        return len(get_pending_signals())
