"""回測統計聚合。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from src.backtest.trade_simulator import STRATEGY_LABEL, TradeResult


@dataclass
class PeriodStats:
    hold_days: int
    label: Optional[str] = None
    sample_count: int = 0
    win_rate: float = 0.0
    avg_return_pct: float = 0.0
    median_return_pct: float = 0.0
    beat_benchmark_rate: float = 0.0
    avg_alpha_pct: float = 0.0


@dataclass
class BacktestSummary:
    source: str  # historical | forward
    period_stats: List[PeriodStats] = field(default_factory=list)
    updated_at: Optional[str] = None
    from_cache: bool = False
    stocks_scanned: int = 0
    stocks_with_data: int = 0
    signal_count: int = 0


def aggregate_trades(trades: List[TradeResult], source: str = "historical") -> BacktestSummary:
    if not trades:
        return BacktestSummary(source=source)

    df = pd.DataFrame(
        [
            {
                "hold_days": t.hold_days,
                "return_pct": t.return_pct,
                "alpha_pct": t.alpha_pct,
                "is_win": t.is_win,
                "beat_benchmark": t.beat_benchmark,
            }
            for t in trades
            if t.valid
        ]
    )

    period_stats: List[PeriodStats] = []
    n = len(df)
    if n > 0:
        period_stats.append(
            PeriodStats(
                hold_days=int(df["hold_days"].max()),
                label=STRATEGY_LABEL,
                sample_count=n,
                win_rate=round(df["is_win"].mean() * 100, 1),
                avg_return_pct=round(df["return_pct"].mean(), 2),
                median_return_pct=round(df["return_pct"].median(), 2),
                beat_benchmark_rate=round(df["beat_benchmark"].mean() * 100, 1),
                avg_alpha_pct=round(df["alpha_pct"].mean(), 2),
            )
        )

    return BacktestSummary(
        source=source,
        period_stats=period_stats,
        updated_at=pd.Timestamp.now().isoformat(),
        from_cache=False,
    )


def format_period_line(stats: PeriodStats) -> str:
    sign = "+" if stats.avg_return_pct >= 0 else ""
    title = stats.label or f"持有{stats.hold_days}日"
    return (
        f"{title}：勝率 {stats.win_rate}%（n={stats.sample_count}）"
        f" | 均報酬 {sign}{stats.avg_return_pct}%"
        f" | 贏0050 {stats.beat_benchmark_rate}%"
    )


def format_backtest_section(summary: Optional[BacktestSummary], title: str) -> str:
    if summary is None or not summary.period_stats:
        return f"--- {title} ---\n尚無資料"

    cache_note = "（快取）" if summary.from_cache else ""
    lines = [f"--- {title}{cache_note} ---"]
    for ps in summary.period_stats:
        lines.append(format_period_line(ps))

    if summary.source == "historical" and summary.stocks_scanned > 0:
        lines.append(
            f"涵蓋 {summary.stocks_with_data}/{summary.stocks_scanned} 檔有資料"
            f"，{summary.signal_count} 個信號"
        )
    elif summary.source == "historical" and summary.stocks_scanned == 0:
        max_n = max(ps.sample_count for ps in summary.period_stats)
        if max_n < 50:
            lines.append("⚠️ 樣本偏少，可能因資料源限流；請執行 --refresh-backtest 重跑")

    return "\n".join(lines)
