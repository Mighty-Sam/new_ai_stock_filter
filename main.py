#!/usr/bin/env python3
"""台股均線回踩選股 — 主程式入口。"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.backtest.historical import get_or_run_backtest
from src.backtest.tracker import ForwardTracker
from src.chart.candlestick import plot_candlestick
from src.data.stock_list import get_stock_list
from src.data.stock_metadata import get_stock_metadata, lookup_metadata
from src.notify.telegram_client import TelegramClient
from src.screener.scanner import scan_market
from src.screener.sector_summary import format_rotation_block

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="台股均線回踩選股")
    parser.add_argument("--dry-run", action="store_true", help="不推播 Telegram，僅輸出結果")
    parser.add_argument("--limit", type=int, default=None, help="限制掃描檔數（測試用）")
    parser.add_argument(
        "--backtest-limit",
        type=int,
        default=None,
        help="限制回測檔數（測試用；不影響今日掃描）",
    )
    parser.add_argument("--workers", type=int, default=8, help="平行執行緒數")
    parser.add_argument("--output-dir", type=str, default="output", help="圖表輸出目錄")
    parser.add_argument("--skip-trading-check", action="store_true", help="略過非交易日檢查")
    parser.add_argument("--refresh-backtest", action="store_true", help="強制重跑 3 年歷史回測")
    parser.add_argument("--skip-backtest", action="store_true", help="跳過歷史回測（使用快取）")
    parser.add_argument(
        "--grade-a-only",
        action="store_true",
        help="僅輸出/推播 A 級（v2 嚴選）",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata = get_stock_metadata()
    scan = scan_market(max_workers=args.workers, stock_limit=args.limit)
    results = scan.grade_a if args.grade_a_only else scan.results

    if not scan.is_trading_day and not args.skip_trading_check:
        msg = f"今日 ({scan.scan_date}) 非交易日，略過掃描。"
        logger.info(msg)
        if not args.dry_run:
            TelegramClient().send_message(f"📊 台股均線回踩選股\n{msg}")
        return 0

    stock_names = get_stock_list()
    chart_paths = {}

    for graded in results:
        result = graded.result
        name = stock_names.get(result.stock_code, "")
        df = scan.price_data.get(result.stock_code)
        if df is None:
            continue
        path = plot_candlestick(
            df=df,
            stock_code=result.stock_code,
            stock_name=name,
            signal_date=result.signal_date,
            output_path=output_dir / f"{result.stock_code}.png",
            grade=graded.grade,
            review_notes=graded.review_notes,
        )
        chart_paths[result.stock_code] = path
        logger.info(
            "符合 [%s]: %s %s | 漲幅 %.1f%% | 回踩 %s",
            graded.grade,
            result.stock_code,
            name,
            result.gain_pct,
            result.retest_ma,
        )

    scan_date_str = scan.scan_date.strftime("%Y/%m/%d")

    tracker = ForwardTracker()
    tracker.record_signals([g.result for g in scan.results], scan.scan_date)
    tracker.settle_matured_trades()
    forward_summary = tracker.get_stats()
    pending_count = tracker.count_pending()

    historical_summary = get_or_run_backtest(
        refresh=args.refresh_backtest,
        skip=args.skip_backtest,
        max_workers=args.workers,
        stock_limit=args.backtest_limit,
    )
    if historical_summary is None and not args.skip_backtest:
        logger.warning("歷史回測資料不可用，略過回測統計")

    if args.dry_run:
        logger.info(
            "Dry run：共 %d 檔（A %d / B %d）",
            len(scan.results),
            len(scan.grade_a),
            len(scan.grade_b),
        )
        for line in format_rotation_block(results, metadata):
            print(line)
        for g in results:
            r = g.result
            meta = lookup_metadata(metadata, r.stock_code)
            print(
                f"  [{g.grade}] {r.stock_code} gain={r.gain_pct}% retest={r.retest_ma} "
                f"產業={meta.industry} 族群={meta.groups_display}"
            )
            for note in g.review_notes:
                print(f"       {note}")
        if historical_summary and historical_summary.period_stats:
            for ps in historical_summary.period_stats:
                print(f"  回測{ps.hold_days}日: 勝率{ps.win_rate}% n={ps.sample_count}")
        return 0

    client = TelegramClient()
    if not client.configured:
        logger.error("請設定 TELEGRAM_BOT_TOKEN 與 TELEGRAM_CHAT_ID")
        return 1

    client.notify_scan_results(
        results=results,
        stock_names=stock_names,
        chart_paths=chart_paths,
        scan_date=scan_date_str,
        metadata=metadata,
        historical_summary=historical_summary,
        forward_summary=forward_summary,
        pending_count=pending_count,
    )
    logger.info("Telegram 推播完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
