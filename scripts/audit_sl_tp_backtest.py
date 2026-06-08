#!/usr/bin/env python3
"""SL/TP 回測結果稽核：驗證成交價、止損止盈邏輯與資料品質。"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest.sl_tp_simulator import MAX_HOLD_DAYS, cross_ref_price
from src.data.price_fetcher import PriceFetcher
from src.indicators.moving_average import add_moving_averages
from src.screener.conditions import evaluate_as_of

TRADES_PATH = Path("data/sl_tp_backtest_trades.csv")
SUMMARY_PATH = Path("data/sl_tp_backtest_summary.json")


def _find_idx(dates: pd.DatetimeIndex, target: date) -> int | None:
    matches = dates.get_indexer([pd.Timestamp(target)], method=None)
    if len(matches) == 0 or matches[0] < 0:
        return None
    return int(matches[0])


def audit_trades(trades: pd.DataFrame, end_date: date) -> dict:
    fetcher = PriceFetcher(delay=0.02)
    price_cache: dict[str, pd.DataFrame] = {}

    errors: list[str] = []
    warnings: list[str] = []
    stale_stocks: set[str] = set()
    checked = 0

    # 每檔抽一筆 + 全部 stop/tp 出場
    sample_keys = trades.groupby("stock_code").head(1)
    priority = trades[trades["exit_reason"].isin(["stop", "take_profit"])]
    to_check = pd.concat([sample_keys, priority]).drop_duplicates()

    for _, row in to_check.iterrows():
        code = str(row["stock_code"])
        if code not in price_cache:
            df = fetcher.fetch(code, end_date=end_date, days=400)
            if df is None:
                warnings.append(f"{code} 無法取得股價")
                continue
            df = add_moving_averages(df)
            price_cache[code] = df
            last = df.index[-1].date()
            if (end_date - last).days > 14:
                stale_stocks.add(code)

        df = price_cache[code]
        sig_idx = _find_idx(df.index, pd.Timestamp(row["signal_date"]).date())
        if sig_idx is None:
            errors.append(f"{code} {row['signal_date']} 找不到信號日")
            continue
        entry_idx = sig_idx + 1
        if entry_idx >= len(df):
            continue

        actual_open = float(df.iloc[entry_idx]["open"])
        entry_price = float(row["entry_price"])
        if abs(actual_open - entry_price) > 0.02:
            errors.append(
                f"{code} {row['signal_date']} 進場價不符："
                f"csv={entry_price} open={actual_open}"
            )

        stop_type = row["stop_type"]
        tp_type = row["tp_type"]
        stop_price = float(row["stop_price"])
        tp_price = float(row["tp_price"])
        expected_tp = entry_price * {"pct_10": 1.1, "pct_20": 1.2, "pct_30": 1.3}[tp_type]
        if abs(tp_price - expected_tp) > 0.02:
            errors.append(f"{code} {row['signal_date']} {tp_type} 止盈價錯誤")

        if stop_type == "pct_5":
            exp_stop = entry_price * 0.95
        elif stop_type == "pct_10":
            exp_stop = entry_price * 0.90
        else:
            exp_stop = row["cross_ref_price"]
        if exp_stop is not None and abs(stop_price - float(exp_stop)) > 0.02:
            errors.append(f"{code} {row['signal_date']} {stop_type} 止損價錯誤")

        exit_reason = row["exit_reason"]
        exit_date = pd.Timestamp(row["exit_date"]).date()
        exit_idx = _find_idx(df.index, exit_date)
        if exit_idx is None:
            errors.append(f"{code} 找不到出場日 {exit_date}")
            continue

        bar = df.iloc[exit_idx]
        low, high = float(bar["low"]), float(bar["high"])
        exit_price = float(row["exit_price"])

        if exit_reason == "stop":
            if low > stop_price + 0.02:
                errors.append(
                    f"{code} {exit_date} 標記止損但 low={low} > stop={stop_price}"
                )
            if abs(exit_price - stop_price) > 0.02:
                errors.append(f"{code} {exit_date} 止損出場價應為 {stop_price}")
        elif exit_reason == "take_profit":
            if high < tp_price - 0.02:
                errors.append(
                    f"{code} {exit_date} 標記止盈但 high={high} < tp={tp_price}"
                )
        elif exit_reason == "timeout":
            expected_hold = exit_idx - entry_idx + 1
            if expected_hold != MAX_HOLD_DAYS:
                errors.append(
                    f"{code} timeout 持有 {expected_hold} 日，應為 {MAX_HOLD_DAYS}"
                )
            if abs(exit_price - float(bar["close"])) > 0.02:
                errors.append(f"{code} {exit_date} timeout 應以收盤價出場")

        ret = (exit_price - entry_price) / entry_price * 100
        if abs(ret - float(row["return_pct"])) > 0.05:
            errors.append(f"{code} 報酬率計算錯誤 csv={row['return_pct']} calc={ret:.2f}")

        checked += 1

    return {
        "checked": checked,
        "errors": errors,
        "warnings": warnings,
        "stale_stocks": sorted(stale_stocks),
        "price_cache_size": len(price_cache),
    }


def analyze_combo_patterns(trades: pd.DataFrame, summary: dict) -> dict:
    notes = []

    # 同一止損、不同止盈統計相同 → 是否因未觸發止盈
    for stop in ("cross_ma", "pct_5", "pct_10"):
        sub = trades[trades["stop_type"] == stop]
        groups = sub.groupby("tp_type")["avg_return_pct" if "avg_return_pct" in sub else "return_pct"]
        tp_rates = sub.groupby("tp_type")["exit_reason"].apply(lambda s: (s == "take_profit").mean())
        if len(tp_rates) and tp_rates.max() < 0.01:
            notes.append(
                f"止損={stop}：三種止盈結果幾乎相同，因 tp_rate≈0（20 日內極少觸及 +10/+20/+30%）"
            )

    cross = trades[trades["stop_type"] == "cross_ma"]
    if len(cross):
        above_entry = (cross["stop_price"] > cross["entry_price"]).mean() * 100
        notes.append(
            f"cross_ma 止損價高於進場價的比例：{above_entry:.1f}%"
            "（上穿均價在進場價之上時，止損變成『獲利保護』而非傳統停損）"
        )
        immediate = cross[cross["hold_days"] == 1]
        notes.append(f"cross_ma 進場當日即出場：{len(immediate)}/{len(cross)} ({len(immediate)/len(cross)*100:.1f}%)")

    dup_signals = trades.groupby(["stock_code", "signal_date", "stop_type"]).size()
    if not all(dup_signals == 3):
        notes.append("警告：同一信號+止損應有 3 筆止盈交易")

    return {"pattern_notes": notes}


def main() -> int:
    if not TRADES_PATH.exists():
        print("找不到 trades CSV，請先執行回測")
        return 1

    trades = pd.read_csv(TRADES_PATH)
    summary = {}
    if SUMMARY_PATH.exists():
        summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))

    end_date = date.today()
    if summary.get("end_date"):
        end_date = date.fromisoformat(summary["end_date"])

    print("=== SL/TP 回測稽核報告 ===")
    print(f"交易筆數：{len(trades)}")
    print(f"信號數：{trades.groupby(['stock_code', 'signal_date']).ngroups}")
    if summary:
        print(
            f"區間：{summary.get('start_date')} ~ {summary.get('end_date')} "
            f"({summary.get('history_years', '?')} 年)"
        )
        print(
            f"覆蓋：{summary.get('stocks_with_data')}/{summary.get('stocks_scanned')} 檔"
        )

    audit = audit_trades(trades, end_date)
    patterns = analyze_combo_patterns(trades, summary)

    print(f"\n--- 資料品質 ---")
    print(f"稽核抽樣：{audit['checked']} 筆")
    print(f"涉及股票：{audit['price_cache_size']} 檔")
    if audit["stale_stocks"]:
        print(f"股價資料過舊（>14 天）：{len(audit['stale_stocks'])} 檔")
        print(f"  範例：{audit['stale_stocks'][:10]}")
    else:
        print("股價資料新鮮度：OK（抽樣無過舊）")

    print(f"\n--- 邏輯驗證 ---")
    if audit["errors"]:
        print(f"發現 {len(audit['errors'])} 個錯誤：")
        for e in audit["errors"][:20]:
            print(f"  [ERROR] {e}")
        if len(audit["errors"]) > 20:
            print(f"  ... 其餘 {len(audit['errors']) - 20} 個")
    else:
        print("抽樣驗證：進場價、止損/止盈價、出場原因與報酬率 — 全部通過")

    if audit["warnings"]:
        for w in audit["warnings"][:10]:
            print(f"  [WARN] {w}")

    print(f"\n--- 九組合統計解讀 ---")
    if summary.get("combo_stats"):
        for cs in summary["combo_stats"]:
            print(
                f"  {cs['stop_type']:8} + {cs['tp_type']:6} | "
                f"n={cs['sample_count']} 勝率={cs['win_rate']}% "
                f"均報酬={cs['avg_return_pct']:+.2f}% "
                f"止損={cs['stop_rate']}% 止盈={cs['tp_rate']}% 到期={cs['timeout_rate']}%"
            )

    print(f"\n--- 模式分析 ---")
    for note in patterns["pattern_notes"]:
        print(f"  • {note}")

    out = Path("data/sl_tp_audit_report.json")
    out.write_text(
        json.dumps(
            {"audit": audit, "patterns": patterns, "summary_meta": summary},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\n完整稽核 JSON：{out}")
    return 0 if not audit["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
