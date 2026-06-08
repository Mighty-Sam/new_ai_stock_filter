#!/usr/bin/env python3
"""2022 vs 2023 年度對照與月度勝率分析 CLI。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest.period_analysis import (
    PRESET_COMBOS,
    build_period_report,
    ensure_backtest_data,
    save_period_report,
)

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


def print_year_comparison(report: dict) -> None:
    years = report["years"]
    benchmarks = report.get("benchmarks", {})
    combo_reports = [r for r in report["combo_reports"] if "error" not in r]

    print()
    print("=== 年度對照 ===")
    print(f"0050 大盤：", end="")
    for y in years:
        b = benchmarks.get(y)
        sign = "+" if b and b >= 0 else ""
        print(f"  {y}={sign}{b}%" if b is not None else f"  {y}=N/A", end="")
    print("\n")

    header = (
        f"{'策略':<28} {'年':>4} {'信號':>5} {'n':>5} "
        f"{'勝率':>7} {'均報酬':>8} {'中位數':>8} {'PF':>6} {'vs大盤':>8}"
    )
    print(header)
    print("-" * len(header))

    for preset_key in PRESET_COMBOS:
        label = PRESET_COMBOS[preset_key].label
        for y in years:
            row = next((r for r in combo_reports if r["preset"] == preset_key and r["year"] == y), None)
            if not row:
                print(f"{label[:28]:<28} {y:>4}  — 缺資料")
                continue
            sign = "+" if row["avg_return_pct"] >= 0 else ""
            alpha = row.get("alpha_vs_benchmark_pct")
            alpha_s = f"{alpha:+.2f}%" if alpha is not None else "N/A"
            print(
                f"{label[:28]:<28} {y:>4} {row['signal_count']:>5} {row['trade_count']:>5} "
                f"{row['win_rate']:>6.1f}% {sign}{row['avg_return_pct']:>7.2f}% "
                f"{row['median_return_pct']:>7.2f}% {_format_pf(row['profit_factor']):>6} {alpha_s:>8}"
            )
    print()


def print_monthly_breakdown(report: dict) -> None:
    years = report["years"]
    combo_reports = [r for r in report["combo_reports"] if "error" not in r]

    for preset_key in PRESET_COMBOS:
        label = PRESET_COMBOS[preset_key].label
        for y in years:
            row = next((r for r in combo_reports if r["preset"] == preset_key and r["year"] == y), None)
            if not row or not row.get("monthly"):
                continue
            print(f"=== 月度勝率：{label}（{y}）===")
            header = f"{'月份':<8} {'信號':>5} {'n':>5} {'勝率':>7} {'均報酬':>8} {'中位數':>8}"
            print(header)
            print("-" * len(header))
            for m in row["monthly"]:
                sign = "+" if m["avg_return_pct"] >= 0 else ""
                print(
                    f"{m['month']:<8} {m['signal_count']:>5} {m['trade_count']:>5} "
                    f"{m['win_rate']:>6.1f}% {sign}{m['avg_return_pct']:>7.2f}% "
                    f"{m['median_return_pct']:>7.2f}%"
                )
            print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="年度對照與月度勝率分析")
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=[2022, 2023],
        help="要比較的年份（預設 2022 2023）",
    )
    parser.add_argument(
        "--refresh-backtest",
        action="store_true",
        help="強制重跑指定年份的 v1/v2 回測",
    )
    parser.add_argument(
        "--skip-backtest",
        action="store_true",
        help="不檢查/執行回測，僅分析既有 CSV",
    )
    parser.add_argument("--workers", type=int, default=8, help="回測平行執行緒數")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    years = sorted(set(args.years))

    if not args.skip_backtest:
        ensure_backtest_data(years, refresh=args.refresh_backtest, max_workers=args.workers)

    report = build_period_report(years)
    out_path = save_period_report(report, years)

    print_year_comparison(report)
    print_monthly_breakdown(report)

    errors = [r for r in report["combo_reports"] if "error" in r]
    if errors:
        print("⚠️ 部分資料缺失：")
        for e in errors:
            print(f"  {e['preset']} {e['year']}: {e['error']}")
        return 1

    print(f"完整報告：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
