"""固定持有期回測：報酬分桶與摘要統計。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from src.backtest.trade_simulator import TradeResult

THRESHOLDS = (10, 20, 30)


@dataclass
class SignalMeta:
    stock_code: str
    grade: str
    retest_ma: str
    volume_ratio: float
    dist_to_high_pct: float
    gain_pct: float


@dataclass
class ReturnBucketReport:
    signal_date: str
    exit_date: str
    signal_count: int
    valid_count: int
    benchmark_return_pct: Optional[float]
    avg_return_pct: float
    median_return_pct: float
    win_rate: float
    beat_benchmark_rate: float
    gain_buckets: Dict[str, int] = field(default_factory=dict)
    loss_buckets: Dict[str, int] = field(default_factory=dict)
    by_grade: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_retest_ma: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    trades: List[Dict[str, Any]] = field(default_factory=list)


def _bucket_counts(returns: List[float]) -> tuple[Dict[str, int], Dict[str, int]]:
    gain: Dict[str, int] = {}
    loss: Dict[str, int] = {}
    for t in THRESHOLDS:
        gain[f">={t}%"] = sum(1 for r in returns if r >= t)
        loss[f"<=-{t}%"] = sum(1 for r in returns if r <= -t)
    return gain, loss


def _subset_stats(returns: List[float], alphas: List[float]) -> Dict[str, Any]:
    if not returns:
        return {"count": 0, "avg_return_pct": 0.0, "win_rate": 0.0, "beat_benchmark_rate": 0.0}
    wins = sum(1 for r in returns if r > 0)
    beats = sum(1 for a in alphas if a > 0)
    return {
        "count": len(returns),
        "avg_return_pct": round(sum(returns) / len(returns), 2),
        "median_return_pct": round(float(pd.Series(returns).median()), 2),
        "win_rate": round(wins / len(returns) * 100, 1),
        "beat_benchmark_rate": round(beats / len(alphas) * 100, 1) if alphas else 0.0,
    }


def build_return_bucket_report(
    trades: List[TradeResult],
    meta_by_code: Dict[str, SignalMeta],
    signal_date: str,
    exit_date: str,
    benchmark_return_pct: Optional[float] = None,
) -> ReturnBucketReport:
    valid = [t for t in trades if t.valid]
    returns = [t.return_pct for t in valid]
    alphas = [t.alpha_pct for t in valid]

    gain_buckets, loss_buckets = _bucket_counts(returns)

    by_grade: Dict[str, Dict[str, Any]] = {}
    by_retest: Dict[str, Dict[str, Any]] = {}
    for grade in ("A", "B"):
        subset = [t for t in valid if meta_by_code.get(t.stock_code, SignalMeta("", grade, "", 0, 0, 0)).grade == grade]
        by_grade[grade] = _subset_stats(
            [t.return_pct for t in subset],
            [t.alpha_pct for t in subset],
        )
    for ma in ("ma5", "ma10"):
        subset = [t for t in valid if meta_by_code.get(t.stock_code, SignalMeta("", "", ma, 0, 0, 0)).retest_ma == ma]
        by_retest[ma] = _subset_stats(
            [t.return_pct for t in subset],
            [t.alpha_pct for t in subset],
        )

    trade_rows: List[Dict[str, Any]] = []
    for t in valid:
        m = meta_by_code.get(t.stock_code)
        trade_rows.append(
            {
                "stock_code": t.stock_code,
                "grade": m.grade if m else "",
                "retest_ma": m.retest_ma if m else "",
                "volume_ratio": m.volume_ratio if m else 0.0,
                "dist_to_high_pct": m.dist_to_high_pct if m else 0.0,
                "signal_gain_pct": m.gain_pct if m else 0.0,
                "entry_date": t.entry_date.isoformat(),
                "entry_price": t.entry_price,
                "exit_date": t.exit_date.isoformat(),
                "exit_price": t.exit_price,
                "hold_days": t.hold_days,
                "return_pct": t.return_pct,
                "benchmark_return_pct": t.benchmark_return_pct,
                "alpha_pct": t.alpha_pct,
                "is_win": t.is_win,
                "beat_benchmark": t.beat_benchmark,
            }
        )
    trade_rows.sort(key=lambda x: (-x["return_pct"], x["stock_code"]))

    return ReturnBucketReport(
        signal_date=signal_date,
        exit_date=exit_date,
        signal_count=len(trades),
        valid_count=len(valid),
        benchmark_return_pct=benchmark_return_pct,
        avg_return_pct=round(sum(returns) / len(returns), 2) if returns else 0.0,
        median_return_pct=round(float(pd.Series(returns).median()), 2) if returns else 0.0,
        win_rate=round(sum(1 for r in returns if r > 0) / len(returns) * 100, 1) if returns else 0.0,
        beat_benchmark_rate=round(sum(1 for a in alphas if a > 0) / len(alphas) * 100, 1) if alphas else 0.0,
        gain_buckets=gain_buckets,
        loss_buckets=loss_buckets,
        by_grade=by_grade,
        by_retest_ma=by_retest,
        trades=trade_rows,
    )


def report_to_dict(report: ReturnBucketReport) -> Dict[str, Any]:
    return {
        "signal_date": report.signal_date,
        "exit_date": report.exit_date,
        "signal_count": report.signal_count,
        "valid_count": report.valid_count,
        "benchmark_return_pct": report.benchmark_return_pct,
        "summary": {
            "avg_return_pct": report.avg_return_pct,
            "median_return_pct": report.median_return_pct,
            "win_rate": report.win_rate,
            "beat_benchmark_rate": report.beat_benchmark_rate,
        },
        "gain_buckets": report.gain_buckets,
        "loss_buckets": report.loss_buckets,
        "by_grade": report.by_grade,
        "by_retest_ma": report.by_retest_ma,
        "trades": report.trades,
    }
