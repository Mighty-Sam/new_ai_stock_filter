#!/usr/bin/env python3
"""v2 策略參數網格回測 CLI。"""

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

from src.backtest.sl_tp_simulator_v2 import ENTRY_LABELS, STOP_LABELS, TP_LABELS
from src.backtest.strategy_grid import get_or_run_strategy_grid

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


def print_top_combos(summary, top_n: int = 10) -> None:
    if not summary.combo_stats:
        print("尚無回測資料")
        return

    rows = sorted(
        summary.combo_stats,
        key=lambda cs: (cs.avg_return_pct, cs.profit_factor or 0),
        reverse=True,
    )[:top_n]

    print()
    print("=== v2 策略參數網格回測 ===")
    period = f"{summary.start_date} ~ {summary.end_date}" if summary.start_date else ""
    print(f"區間：{period}（{summary.history_years} 年）")
    print(
        f"涵蓋 {summary.stocks_with_data}/{summary.stocks_scanned} 檔，"
        f"{summary.total_signals_v2} 信號，{summary.trade_count} 筆交易，"
        f"{len(summary.combo_stats)} 組合"
    )
    if summary.from_cache:
        print(f"（快取，更新於 {summary.updated_at}）")
    print()

    header = (
        f"{'整理≥':>4} {'止損':<10} {'止盈':<6} {'持有':>4} {'進場':<8} "
        f"{'n':>6} {'勝率':>7} {'均報酬':>8} {'PF':>6} {'盈虧比':>6}"
    )
    print(f"Top {top_n}（依均報酬）")
    print(header)
    print("-" * len(header))

    for cs in rows:
        sign = "+" if cs.avg_return_pct >= 0 else ""
        print(
            f"{cs.min_oscillation:>4} {STOP_LABELS.get(cs.stop_type, cs.stop_type):<10} "
            f"{TP_LABELS.get(cs.tp_type, cs.tp_type):<6} {cs.max_hold_days:>4} "
            f"{ENTRY_LABELS.get(cs.entry_mode, cs.entry_mode):<8} "
            f"{cs.sample_count:>6} {cs.win_rate:>6.1f}% "
            f"{sign}{cs.avg_return_pct:>7.2f}% "
            f"{_format_pf(cs.profit_factor):>6} "
            f"{_format_pf(cs.avg_win_loss_ratio):>6}"
        )
    print()
    tag = ""
    if summary.start_date and summary.end_date:
        y0, y1 = str(summary.start_date)[:4], str(summary.end_date)[:4]
        if y0 == y1:
            tag = f"_{y0}"
    print(f"明細：data/strategy_grid_trades{tag}.csv")
    print(f"摘要：data/strategy_grid_summary{tag}.json")


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="v2 策略參數網格回測")
    parser.add_argument("--refresh", action="store_true", help="強制重跑")
    parser.add_argument("--years", type=int, default=1, help="回測年數（未指定 --from/--to 時使用）")
    parser.add_argument("--from", dest="from_date", type=_parse_date, default=None, metavar="YYYY-MM-DD", help="信號區間起日")
    parser.add_argument("--to", dest="to_date", type=_parse_date, default=None, metavar="YYYY-MM-DD", help="信號區間迄日")
    parser.add_argument("--limit", type=int, default=None, help="限制回測檔數")
    parser.add_argument("--workers", type=int, default=8, help="平行執行緒數")
    parser.add_argument("--top", type=int, default=10, help="顯示 Top N 組合")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    summary = get_or_run_strategy_grid(
        refresh=args.refresh,
        max_workers=args.workers,
        stock_limit=args.limit,
        history_years=args.years,
        start_date=args.from_date,
        end_date=args.to_date,
    )
    print_top_combos(summary, top_n=args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
