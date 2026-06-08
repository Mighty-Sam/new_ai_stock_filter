#!/usr/bin/env python3
"""止損/止盈組合回測 CLI。"""

from __future__ import annotations

import argparse
from datetime import date
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest.sl_tp_backtest import get_or_run_sl_tp_backtest
from src.backtest.sl_tp_simulator import STOP_LABELS, TP_LABELS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _format_pf(value: float | None) -> str:
    if value is None:
        return "N/A"
    if value == float("inf"):
        return "inf"
    return f"{value:.2f}"


def print_summary_table(summary) -> None:
    if not summary.combo_stats:
        print("尚無回測資料")
        return

    rows = sorted(
        summary.combo_stats,
        key=lambda cs: (cs.avg_return_pct, cs.profit_factor or 0),
        reverse=True,
    )

    header = (
        f"{'止損':<8} {'止盈':<6} {'n':>6} {'勝率':>7} {'均報酬':>8} "
        f"{'PF':>6} {'盈虧比':>6} {'止損%':>6} {'止盈%':>6} {'到期%':>6}"
    )
    print()
    print("=== 止損/止盈組合回測 ===")
    years = summary.history_years
    period = f"{summary.start_date} ~ {summary.end_date}" if summary.start_date else f"近 {years} 年"
    print(f"區間：{period}")
    print(
        f"涵蓋 {summary.stocks_with_data}/{summary.stocks_scanned} 檔有資料，"
        f"{summary.signal_count} 個信號，{summary.trade_count} 筆交易"
    )
    if summary.from_cache:
        print(f"（快取，更新於 {summary.updated_at}）")
    print()
    print(header)
    print("-" * len(header))

    for cs in rows:
        sign = "+" if cs.avg_return_pct >= 0 else ""
        print(
            f"{STOP_LABELS[cs.stop_type]:<8} {TP_LABELS[cs.tp_type]:<6} "
            f"{cs.sample_count:>6} {cs.win_rate:>6.1f}% "
            f"{sign}{cs.avg_return_pct:>7.2f}% "
            f"{_format_pf(cs.profit_factor):>6} "
            f"{_format_pf(cs.avg_win_loss_ratio):>6} "
            f"{cs.stop_rate:>5.1f}% {cs.tp_rate:>5.1f}% {cs.timeout_rate:>5.1f}%"
        )
    print()
    print(f"明細：data/sl_tp_backtest_trades{('_' + str(summary.start_date)[:4]) if summary.start_date and summary.end_date and str(summary.start_date)[:4] == str(summary.end_date)[:4] else ''}.csv")
    print(f"摘要：data/sl_tp_backtest_summary{('_' + str(summary.start_date)[:4]) if summary.start_date and summary.end_date and str(summary.start_date)[:4] == str(summary.end_date)[:4] else ''}.json")


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="止損/止盈組合回測")
    parser.add_argument("--refresh", action="store_true", help="強制重跑回測")
    parser.add_argument("--limit", type=int, default=None, help="限制回測檔數（測試用）")
    parser.add_argument("--years", type=int, default=3, help="回測年數（未指定 --from/--to 時使用）")
    parser.add_argument("--from", dest="from_date", type=_parse_date, default=None, metavar="YYYY-MM-DD", help="信號區間起日")
    parser.add_argument("--to", dest="to_date", type=_parse_date, default=None, metavar="YYYY-MM-DD", help="信號區間迄日")
    parser.add_argument("--workers", type=int, default=8, help="平行執行緒數")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    summary = get_or_run_sl_tp_backtest(
        refresh=args.refresh,
        max_workers=args.workers,
        stock_limit=args.limit,
        history_years=args.years,
        start_date=args.from_date,
        end_date=args.to_date,
    )
    print_summary_table(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
