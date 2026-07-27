#!/usr/bin/env python3
"""漲停量縮整理 歷史回測 CLI。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest.limit_up_contraction_backtest import get_or_run_limit_up_contraction_backtest
from src.backtest.stats import format_period_line

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def print_summary(summary) -> None:
    print()
    print("=== 漲停量縮整理 歷史回測 ===")
    if summary.from_cache:
        print(f"（快取，更新於 {summary.updated_at}）")
    print(
        f"涵蓋 {summary.stocks_with_data}/{summary.stocks_scanned} 檔有資料，"
        f"{summary.signal_count} 個信號"
    )
    if not summary.period_stats:
        print("尚無回測資料（可能無符合條件的歷史信號）")
        return
    for ps in summary.period_stats:
        print(format_period_line(ps))
    print()
    print("明細：data/limit_up_contraction_backtest_trades.csv")
    print("摘要：data/limit_up_contraction_backtest_summary.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="漲停量縮整理歷史回測")
    parser.add_argument("--refresh", action="store_true", help="強制重跑回測")
    parser.add_argument("--limit", type=int, default=None, help="限制回測檔數（測試用）")
    parser.add_argument("--workers", type=int, default=8, help="平行執行緒數")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    summary = get_or_run_limit_up_contraction_backtest(
        refresh=args.refresh,
        max_workers=args.workers,
        stock_limit=args.limit,
    )
    print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
