#!/usr/bin/env python3
"""長下影線反轉 歷史回測 CLI。"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest.shadow_reversal_backtest import (
    get_or_run_shadow_reversal_backtest,
    output_paths,
    resolve_backtest_window,
)
from src.backtest.stats import format_period_line

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _profit_factor(returns: pd.Series) -> Optional[float]:
    wins = returns[returns > 0].sum()
    losses = returns[returns < 0].sum()
    if losses == 0:
        return None if wins == 0 else float("inf")
    return round(wins / abs(losses), 2)


def _avg_win_loss_ratio(returns: pd.Series) -> Optional[float]:
    win_mean = returns[returns > 0].mean()
    loss_mean = returns[returns < 0].mean()
    if pd.isna(win_mean) or pd.isna(loss_mean) or loss_mean == 0:
        return None
    return round(win_mean / abs(loss_mean), 2)


def print_summary(summary, trades_path: Path, summary_path: Path) -> None:
    print()
    print("=== 長下影線反轉 歷史回測 ===")
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

    if trades_path.exists():
        trades = pd.read_csv(trades_path)
        if not trades.empty:
            pf = _profit_factor(trades["return_pct"])
            wl = _avg_win_loss_ratio(trades["return_pct"])
            print(f"Profit Factor：{pf if pf is not None else 'N/A'} | 盈虧比：{wl if wl is not None else 'N/A'}")

    print()
    print(f"明細：{trades_path}")
    print(f"摘要：{summary_path}")


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="長下影線反轉歷史回測")
    parser.add_argument("--refresh", action="store_true", help="強制重跑回測")
    parser.add_argument("--limit", type=int, default=None, help="限制回測檔數（測試用）")
    parser.add_argument("--years", type=int, default=3, help="回測年數（未指定 --from/--to 時使用）")
    parser.add_argument("--from", dest="from_date", type=_parse_date, default=None, metavar="YYYY-MM-DD", help="信號區間起日")
    parser.add_argument("--to", dest="to_date", type=_parse_date, default=None, metavar="YYYY-MM-DD", help="信號區間迄日")
    parser.add_argument("--workers", type=int, default=8, help="平行執行緒數")
    parser.add_argument("--tag", type=str, default=None, help="輸出檔名標籤（覆蓋自動判斷，避免不同區間撞名）")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    summary = get_or_run_shadow_reversal_backtest(
        refresh=args.refresh,
        max_workers=args.workers,
        stock_limit=args.limit,
        history_years=args.years,
        start_date=args.from_date,
        end_date=args.to_date,
        period_tag=args.tag,
    )

    start, end = resolve_backtest_window(args.years, args.from_date, args.to_date)
    tag = args.tag
    if tag is None and (args.from_date or args.to_date):
        tag = f"{start.year}" if start.year == end.year else f"{start.year}_{end.year}"
    summary_path, trades_path = output_paths(tag)

    print_summary(summary, trades_path, summary_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
