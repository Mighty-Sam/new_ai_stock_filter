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
from src.screener.optimized_filter import filter_optimized
from src.screener.scanner import scan_market
from src.screener.sector_summary import format_rotation_block, format_theme_rotation_block
from src.screener.theme_scanner import scan_theme_momentum

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
    parser.add_argument("--refresh-backtest", action="store_true", help="強制重跑 3 年歷史回測（離線分析用）")
    parser.add_argument("--skip-backtest", action="store_true", help="跳過歷史回測")
    parser.add_argument(
        "--grade-a-only",
        action="store_true",
        help="僅輸出/推播 A 級（v2 嚴選）",
    )
    parser.add_argument(
        "--legacy-v1-all",
        action="store_true",
        help="使用優化前規則（全部 A+B，不套用優化篩選）",
    )
    parser.add_argument(
        "--skip-theme",
        action="store_true",
        help="略過低位題材動能選股",
    )
    parser.add_argument(
        "--enable-theme",
        action="store_true",
        help="啟用低位題材動能選股推播",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    skip_theme = args.skip_theme or not args.enable_theme

    output_dir = Path(args.output_dir)
    ma_output_dir = output_dir
    theme_output_dir = output_dir / "theme"
    ma_output_dir.mkdir(parents=True, exist_ok=True)
    theme_output_dir.mkdir(parents=True, exist_ok=True)

    metadata = get_stock_metadata()
    scan = scan_market(max_workers=args.workers, stock_limit=args.limit)
    v1_total = len(scan.results)
    if args.grade_a_only:
        results = scan.grade_a
    elif args.legacy_v1_all:
        results = scan.results
    else:
        results = filter_optimized(scan.results)
        logger.info(
            "優化篩選：%d 檔（原 v1 共 %d 檔）",
            len(results),
            v1_total,
        )

    theme_scan = None
    if not skip_theme:
        theme_scan = scan_theme_momentum(max_workers=args.workers, stock_limit=args.limit)

    if not scan.is_trading_day and not args.skip_trading_check:
        msg = f"今日 ({scan.scan_date}) 非交易日，略過掃描。"
        logger.info(msg)
        if not args.dry_run:
            TelegramClient().send_message(f"📊 台股均線回踩選股（優化版）\n{msg}")
        return 0

    stock_names = get_stock_list()
    chart_paths = {}
    theme_chart_paths = {}

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
            output_path=ma_output_dir / f"{result.stock_code}.png",
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

    if theme_scan is not None:
        for tr in theme_scan.results:
            name = stock_names.get(tr.stock_code, "")
            df = theme_scan.price_data.get(tr.stock_code)
            if df is None:
                continue
            path = plot_candlestick(
                df=df,
                stock_code=tr.stock_code,
                stock_name=name,
                signal_date=tr.signal_date,
                output_path=theme_output_dir / f"{tr.stock_code}.png",
                grade=None,
                review_notes=tr.review_notes,
            )
            theme_chart_paths[tr.stock_code] = path
            logger.info(
                "題材動能: %s %s | 20日漲幅 %.1f%% | 市值 %.1f億",
                tr.stock_code,
                name,
                tr.gain_20d_pct,
                tr.market_cap_billions,
            )

    scan_date_str = scan.scan_date.strftime("%Y/%m/%d")

    tracker = ForwardTracker()
    tracker.record_signals([g.result for g in results], scan.scan_date)
    settled = tracker.settle_matured_trades(as_of=scan.scan_date)
    today_settled = [t for t in settled if t.exit_date == scan.scan_date]
    forward_summary = tracker.get_stats()
    pending_count = tracker.count_pending()

    if args.refresh_backtest:
        historical_summary = get_or_run_backtest(
            refresh=True,
            skip=False,
            max_workers=args.workers,
            stock_limit=args.backtest_limit,
        )
        if historical_summary and historical_summary.period_stats:
            for ps in historical_summary.period_stats:
                title = ps.label or f"回測{ps.hold_days}日"
                logger.info("歷史回測 %s: 勝率%.1f%% n=%d", title, ps.win_rate, ps.sample_count)

    if args.dry_run:
        client = TelegramClient()
        logger.info(
            "Dry run 均線回踩（優化版）：v1 %d 檔 → 優化後 %d 檔",
            v1_total,
            len(results),
        )
        print(client.format_summary(results, stock_names, scan_date_str, metadata, v1_total=v1_total))
        print()
        print(
            client.format_forward_backtest(
                scan_date_str,
                forward_summary,
                today_settled,
                pending_count,
            )
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

        if theme_scan is not None:
            print()
            logger.info(
                "Dry run 題材動能：%d 檔（第一階段 %d）",
                len(theme_scan.results),
                theme_scan.stage1_count,
            )
            if theme_scan.hot_industries:
                print(f"熱門產業：{'、'.join(theme_scan.hot_industries)}")
            for line in format_theme_rotation_block(theme_scan.results):
                print(line)
            for tr in theme_scan.results:
                print(
                    f"  {tr.stock_code} close={tr.close} gain20={tr.gain_20d_pct}% "
                    f"cap={tr.market_cap_billions}億 hold={tr.director_holding_pct}% "
                    f"產業={tr.industry}"
                )
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
        v1_total=v1_total if not args.legacy_v1_all and not args.grade_a_only else 0,
    )
    logger.info("均線回踩 Telegram 推播完成")

    client.notify_forward_backtest(
        scan_date=scan_date_str,
        forward_summary=forward_summary,
        today_settled=today_settled,
        pending_count=pending_count,
    )
    logger.info("前瞻回測 Telegram 推播完成")

    if theme_scan is not None:
        client.notify_theme_results(
            results=theme_scan.results,
            stock_names=stock_names,
            chart_paths=theme_chart_paths,
            scan_date=scan_date_str,
            hot_industries=theme_scan.hot_industries,
            stage1_count=theme_scan.stage1_count,
        )
        logger.info("題材動能 Telegram 推播完成")

    return 0


if __name__ == "__main__":
    sys.exit(main())
