#!/usr/bin/env python3
"""策略一（均線回踩）指定信號日 → 隔日開盤買 → 固定出場日收盤賣 回測 CLI。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest.return_buckets import (
    SignalMeta,
    build_return_bucket_report,
    report_to_dict,
)
from src.backtest.trade_simulator import simulate_fixed_exit, simulate_trade
from src.data.benchmark import fetch_benchmark
from src.data.price_fetcher import PriceFetcher
from src.data.stock_list import get_stock_list
from src.indicators.moving_average import add_moving_averages
from src.screener.optimized_filter import filter_optimized
from src.screener.scanner import scan_market

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

REPORTS_DIR = Path("data/reports")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _benchmark_period_return(benchmark_df: pd.DataFrame, entry_date: date, exit_date: date) -> float | None:
    from src.backtest.trade_simulator import _benchmark_return

    r = _benchmark_return(benchmark_df, entry_date, exit_date)
    return round(r * 100, 2) if r is not None else None


def _report_stem(signal_date: date, exit_date: date, optimized: bool) -> str:
    base = f"signal_hold_{signal_date:%Y%m%d}_{exit_date:%Y%m%d}"
    return f"{base}_optimized" if optimized else base


def run_analysis(
    signal_date: date,
    exit_date: date,
    stock_limit: int | None = None,
    compare_sl_tp: bool = False,
    optimized: bool = True,
    list_only: bool = False,
) -> dict:
    if exit_date <= signal_date:
        raise ValueError("exit_date 必須晚於 signal_date")

    logger.info("掃描信號日 %s（策略一：均線回踩）", signal_date)
    scan = scan_market(end_date=signal_date, stock_limit=stock_limit)
    v1_count = len(scan.results)
    signals = filter_optimized(scan.results) if optimized else scan.results
    logger.info(
        "信號池 %d 檔（v1 共 %d 檔%s）",
        len(signals),
        v1_count,
        "，已套用優化篩選" if optimized else "",
    )

    stock_names = get_stock_list()

    if list_only:
        rows = []
        for g in signals:
            rows.append(
                {
                    "stock_code": g.stock_code,
                    "stock_name": stock_names.get(g.stock_code, g.stock_code),
                    "grade": g.grade,
                    "retest_ma": g.retest_ma,
                    "gain_pct": g.gain_pct,
                    "volume_ratio": g.volume_ratio,
                    "dist_to_high_pct": g.dist_to_high_pct,
                }
            )
        return {
            "signal_date": signal_date.isoformat(),
            "exit_date": exit_date.isoformat(),
            "optimized": optimized,
            "v1_signal_count": v1_count,
            "signal_count": len(signals),
            "valid_count": 0,
            "list_only": True,
            "signals": rows,
        }

    fetcher = PriceFetcher(delay=0.05)
    benchmark_df = fetch_benchmark()
    benchmark_df = add_moving_averages(benchmark_df)

    meta_by_code: dict[str, SignalMeta] = {}
    trades = []
    sl_tp_comparison = []

    fetch_end = exit_date
    if compare_sl_tp:
        fetch_end = exit_date + timedelta(days=30)

    for graded in signals:
        code = graded.stock_code
        meta_by_code[code] = SignalMeta(
            stock_code=code,
            grade=graded.grade,
            retest_ma=graded.retest_ma,
            volume_ratio=graded.volume_ratio,
            dist_to_high_pct=graded.dist_to_high_pct,
            gain_pct=graded.gain_pct,
        )

        df = fetcher.fetch(code, end_date=fetch_end, days=250)
        if df is None or df.empty:
            logger.warning("%s 無價格資料，略過", code)
            continue
        df = add_moving_averages(df)

        trade = simulate_fixed_exit(code, df, benchmark_df, signal_date, exit_date)
        if trade is None:
            logger.warning("%s 無法模擬固定出場，略過", code)
            continue
        trades.append(trade)

        if compare_sl_tp:
            sl_tp = simulate_trade(code, df, benchmark_df, signal_date)
            if sl_tp is not None:
                sl_tp_comparison.append(
                    {
                        "stock_code": code,
                        "fixed_exit_pct": trade.return_pct,
                        "sl_tp_pct": sl_tp.return_pct,
                        "sl_tp_reason": sl_tp.exit_reason,
                        "delta_pct": round(trade.return_pct - sl_tp.return_pct, 2),
                    }
                )

    valid_trades = [t for t in trades if t.valid]
    bench_pct = None
    if valid_trades:
        bench_pct = _benchmark_period_return(
            benchmark_df,
            valid_trades[0].entry_date,
            exit_date,
        )

    report = build_return_bucket_report(
        trades,
        meta_by_code,
        signal_date.isoformat(),
        exit_date.isoformat(),
        benchmark_return_pct=bench_pct,
    )

    for row in report.trades:
        row["stock_name"] = stock_names.get(row["stock_code"], row["stock_code"])

    result = report_to_dict(report)
    result["sl_tp_comparison"] = sl_tp_comparison
    result["optimized"] = optimized
    result["v1_signal_count"] = v1_count
    return result


def save_report(
    result: dict,
    signal_date: date,
    exit_date: date,
    optimized: bool = True,
) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = _report_stem(signal_date, exit_date, optimized)
    json_path = REPORTS_DIR / f"{stem}.json"
    csv_path = REPORTS_DIR / f"{stem}.csv"

    payload = {**result, "updated_at": datetime.now().isoformat()}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    trades = result.get("trades") or []
    if trades:
        pd.DataFrame(trades).to_csv(csv_path, index=False, encoding="utf-8-sig")
    else:
        csv_path.write_text("", encoding="utf-8-sig")

    return json_path, csv_path


def print_summary(result: dict, signal_date: date, exit_date: date) -> None:
    print()
    print(f"=== {signal_date} 信號 → 隔日開買 → {exit_date} 收賣 ===")
    print(
        f"信號 {result['signal_count']} 檔 | "
        f"有效模擬 {result['valid_count']} 檔 | "
        f"0050 同期 {result.get('benchmark_return_pct', 'N/A')}%"
    )
    print(
        f"平均 {result['summary']['avg_return_pct']}% | "
        f"中位數 {result['summary']['median_return_pct']}% | "
        f"勝率 {result['summary']['win_rate']}% | "
        f"打敗大盤 {result['summary']['beat_benchmark_rate']}%"
    )

    print("\n分桶（累計）:")
    gain = result.get("gain_buckets") or {}
    loss = result.get("loss_buckets") or {}
    print(
        f"  漲 ≥10%: {gain.get('>=10%', 0)} | "
        f"≥20%: {gain.get('>=20%', 0)} | "
        f"≥30%: {gain.get('>=30%', 0)}"
    )
    print(
        f"  跌 ≤-10%: {loss.get('<=-10%', 0)} | "
        f"≤-20%: {loss.get('<=-20%', 0)} | "
        f"≤-30%: {loss.get('<=-30%', 0)}"
    )

    print("\nA/B 分拆:")
    for grade, stats in (result.get("by_grade") or {}).items():
        print(
            f"  [{grade}] {stats['count']} 檔 | "
            f"均報酬 {stats['avg_return_pct']}% | 勝率 {stats['win_rate']}%"
        )

    print("\n回踩均線分拆:")
    for ma, stats in (result.get("by_retest_ma") or {}).items():
        label = "MA5" if ma == "ma5" else "MA10"
        print(
            f"  [{label}] {stats['count']} 檔 | "
            f"均報酬 {stats['avg_return_pct']}% | 勝率 {stats['win_rate']}%"
        )

    trades = result.get("trades") or []
    if trades:
        print("\n個股報酬:")
        for row in trades:
            sign = "+" if row["return_pct"] >= 0 else ""
            name = row.get("stock_name", row["stock_code"])
            print(
                f"  {row['stock_code']} {name} [{row['grade']}] "
                f"{sign}{row['return_pct']}% "
                f"(0050 {row['benchmark_return_pct']}%, alpha {row['alpha_pct']}%)"
            )

    sl_tp = result.get("sl_tp_comparison") or []
    if sl_tp:
        print("\n固定出場 vs 停損停利（-10%/+30%）:")
        for row in sl_tp:
            print(
                f"  {row['stock_code']} 固定 {row['fixed_exit_pct']}% | "
                f"SL/TP {row['sl_tp_pct']}% ({row['sl_tp_reason']}) | "
                f"差 {row['delta_pct']}%"
            )
    print()


def print_signal_list(result: dict, signal_date: date) -> None:
    print()
    label = "優化後" if result.get("optimized") else "v1 全檔"
    print(f"=== {signal_date} 策略一信號清單（{label}）===")
    print(f"v1 共 {result.get('v1_signal_count', '?')} 檔 → 篩選後 {result['signal_count']} 檔")
    print()
    for i, row in enumerate(result.get("signals") or [], 1):
        ma = "MA5" if row["retest_ma"] == "ma5" else "MA10"
        print(
            f"{i:2}. [{row['grade']}] {row['stock_code']} {row['stock_name']} | "
            f"回踩 {ma} | 20K漲幅 {row['gain_pct']}% | "
            f"量比 {row['volume_ratio']}× | 距高 {row['dist_to_high_pct']}%"
        )
    print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="策略一信號日固定持有期回測")
    parser.add_argument("--signal-date", required=True, help="信號日 YYYY-MM-DD")
    parser.add_argument("--exit-date", required=True, help="出場日 YYYY-MM-DD（收盤賣）")
    parser.add_argument("--limit", type=int, default=None, help="限制掃描檔數（測試用）")
    parser.add_argument(
        "--compare-sl-tp",
        action="store_true",
        help="附加固定出場 vs 停損停利對照",
    )
    parser.add_argument(
        "--legacy-v1-all",
        action="store_true",
        help="不套用優化篩選（全部 A+B）",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="僅輸出信號清單，不做持有期回測",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    signal_date = _parse_date(args.signal_date)
    exit_date = _parse_date(args.exit_date)
    optimized = not args.legacy_v1_all

    result = run_analysis(
        signal_date,
        exit_date,
        stock_limit=args.limit,
        compare_sl_tp=args.compare_sl_tp,
        optimized=optimized,
        list_only=args.list_only,
    )

    if args.list_only:
        print_signal_list(result, signal_date)
        json_path = REPORTS_DIR / f"{_report_stem(signal_date, exit_date, optimized)}_list.json"
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps({**result, "updated_at": datetime.now().isoformat()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"清單已寫入: {json_path}")
        return

    json_path, csv_path = save_report(result, signal_date, exit_date, optimized=optimized)
    print_summary(result, signal_date, exit_date)
    print(f"報告已寫入:\n  {json_path}\n  {csv_path}")


if __name__ == "__main__":
    main()
