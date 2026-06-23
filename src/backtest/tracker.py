"""前瞻信號追蹤與結算。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

import pandas as pd

from src.backtest.stats import BacktestSummary, aggregate_trades
from src.backtest.storage import (
    OPTIMIZED_DB_PATH,
    get_all_outcomes,
    get_outcomes_by_signal_date,
    get_pending_signals,
    insert_outcome,
    insert_signal,
    mark_signal_status,
    update_signal_entry,
)
from src.backtest.trade_simulator import (
    MAX_HOLD_DAYS,
    ExitReason,
    TradeResult,
    simulate_trade,
    simulate_trades,
)
from src.data.benchmark import fetch_benchmark
from src.data.price_fetcher import PriceFetcher
from src.data.trading_calendar import offset_trading_days
from src.screener.conditions import ScreenResult
from src.screener.scanner import scan_market

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
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    return_pct: float
    exit_reason: ExitReason
    hold_days: int


@dataclass
class MaturityCohortReport:
    scan_date: date
    signal_date: Optional[date]
    trades: List[SettledTrade] = field(default_factory=list)
    summary: Optional[BacktestSummary] = None
    lookback_days: int = MAX_HOLD_DAYS

    @property
    def is_warmup(self) -> bool:
        return self.signal_date is None

    @property
    def has_trades(self) -> bool:
        return len(self.trades) > 0


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

    def _fetch_stock_df(
        self,
        stock_code: str,
        end_date: Optional[date] = None,
    ) -> Optional[pd.DataFrame]:
        return self.fetcher.fetch(stock_code, days=80, end_date=end_date)

    def settle_matured_trades(self, as_of: Optional[date] = None) -> List[SettledTrade]:
        pending = get_pending_signals(db_path=self.db_path)
        settled: List[SettledTrade] = []
        today = as_of or date.today()

        for row in pending:
            signal_id = int(row["id"])
            stock_code = row["stock_code"]
            signal_date = date.fromisoformat(row["signal_date"])

            df = self._fetch_stock_df(stock_code, end_date=today)
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
                    entry_date=trade.entry_date,
                    entry_price=trade.entry_price,
                    exit_date=trade.exit_date,
                    exit_price=trade.exit_price,
                    return_pct=trade.return_pct,
                    exit_reason=trade.exit_reason,
                    hold_days=trade.hold_days,
                )
            )
            logger.info("結算信號 %s %s（%s）", stock_code, signal_date, trade.exit_reason)

        if settled:
            logger.info("前瞻追蹤：結算 %d 筆信號", len(settled))
        return settled

    def _row_to_settled(self, row) -> SettledTrade:
        return SettledTrade(
            stock_code=row["stock_code"],
            signal_date=date.fromisoformat(row["signal_date"]),
            entry_date=date.fromisoformat(row["entry_date"]),
            entry_price=float(row["entry_price"]),
            exit_date=date.fromisoformat(row["exit_date"]),
            exit_price=float(row["exit_price"]),
            return_pct=float(row["return_pct"]),
            exit_reason=row["exit_reason"] or "timeout",
            hold_days=int(row["hold_days"]),
        )

    def _trade_to_settled(self, trade: TradeResult) -> SettledTrade:
        return SettledTrade(
            stock_code=trade.stock_code,
            signal_date=trade.signal_date,
            entry_date=trade.entry_date,
            entry_price=trade.entry_price,
            exit_date=trade.exit_date,
            exit_price=trade.exit_price,
            return_pct=trade.return_pct,
            exit_reason=trade.exit_reason,
            hold_days=trade.hold_days,
        )

    def _rows_to_trade_results(self, rows) -> List[TradeResult]:
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
        return trades

    def _backfill_cohort(
        self,
        signal_date: date,
        as_of: date,
    ) -> tuple[List[SettledTrade], List[TradeResult]]:
        logger.info("批次回測：DB 無 %s 資料，改以歷史掃描回補", signal_date)
        scan = scan_market(end_date=signal_date)
        picks = scan.grade_a
        if not picks:
            return [], []

        settled: List[SettledTrade] = []
        trade_results: List[TradeResult] = []
        for graded in picks:
            code = graded.stock_code
            df = self._fetch_stock_df(code, end_date=as_of)
            if df is None or df.empty:
                continue
            trade = simulate_trade(code, df, self.benchmark_df, signal_date)
            if trade is None or not trade.valid:
                continue
            if trade.exit_date > as_of:
                continue
            trade_results.append(trade)
            settled.append(self._trade_to_settled(trade))
        logger.info("批次回測回補：信號日 %s 共 %d 檔", signal_date, len(settled))
        return settled, trade_results

    def get_maturity_cohort(self, as_of: date) -> MaturityCohortReport:
        self.settle_matured_trades(as_of=as_of)
        signal_date = offset_trading_days(as_of, -MAX_HOLD_DAYS, self.benchmark_df)
        if signal_date is None:
            return MaturityCohortReport(scan_date=as_of, signal_date=None)

        rows = get_outcomes_by_signal_date(signal_date, db_path=self.db_path)
        if rows:
            settled = [self._row_to_settled(r) for r in rows]
            trade_results = self._rows_to_trade_results(rows)
        else:
            settled, trade_results = self._backfill_cohort(signal_date, as_of)

        summary = aggregate_trades(trade_results, source="forward") if trade_results else None
        return MaturityCohortReport(
            scan_date=as_of,
            signal_date=signal_date,
            trades=sorted(settled, key=lambda t: t.stock_code),
            summary=summary,
        )

    def get_stats(self) -> BacktestSummary:
        rows = get_all_outcomes(db_path=self.db_path)
        trades = self._rows_to_trade_results(rows)
        return aggregate_trades(trades, source="forward")

    def count_pending(self) -> int:
        return len(get_pending_signals(db_path=self.db_path))
