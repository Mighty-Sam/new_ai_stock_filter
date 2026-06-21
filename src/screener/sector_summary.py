"""今日信號產業 / 族群分布統計。"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Tuple

from src.data.stock_metadata import MISSING, StockMetadata, lookup_metadata
from src.screener.grading import GradedScreenResult
from src.screener.theme_conditions import ThemeScreenResult

TOP_N = 8


def _format_distribution(counter: Counter[str], total: int, top_n: int = TOP_N) -> str:
    if not counter or total == 0:
        return MISSING

    ranked = sorted(counter.items(), key=lambda x: (-x[1], x[0]))
    parts: List[str] = []
    shown = 0
    other_count = 0

    for name, count in ranked[:top_n]:
        parts.append(f"{name}({count})")
        shown += count

    if len(ranked) > top_n:
        other_count = total - shown
        if other_count > 0:
            parts.append(f"其他({other_count})")

    return " | ".join(parts)


def build_rotation_summary(
    results: List[GradedScreenResult],
    metadata: Dict[str, StockMetadata],
) -> Tuple[str, str]:
    """回傳 (產業分布行, 族群分布行)。"""
    industry_counter: Counter[str] = Counter()
    group_counter: Counter[str] = Counter()

    for g in results:
        meta = lookup_metadata(metadata, g.stock_code)
        industry = meta.industry if meta.industry != MISSING else "未知"
        industry_counter[industry] += 1
        if meta.groups:
            for grp in meta.groups:
                group_counter[grp] += 1
        else:
            group_counter["未知"] += 1

    n = len(results)
    industry_line = _format_distribution(industry_counter, n)
    group_line = _format_distribution(group_counter, n)
    return industry_line, group_line


def format_rotation_block(
    results: List[GradedScreenResult],
    metadata: Dict[str, StockMetadata],
) -> List[str]:
    if not results:
        return []
    industry_line, group_line = build_rotation_summary(results, metadata)
    return [
        "【今日分布】",
        f"產業：{industry_line}",
        f"族群：{group_line}",
        "",
    ]


def build_theme_rotation_summary(
    results: List[ThemeScreenResult],
) -> Tuple[str, str]:
    """題材動能：結果已內含 industry / groups。"""
    industry_counter: Counter[str] = Counter()
    group_counter: Counter[str] = Counter()

    for r in results:
        industry_counter[r.industry] += 1
        if r.groups:
            for grp in r.groups:
                group_counter[grp] += 1
        else:
            group_counter["未知"] += 1

    n = len(results)
    return (
        _format_distribution(industry_counter, n),
        _format_distribution(group_counter, n),
    )


def format_theme_rotation_block(results: List[ThemeScreenResult]) -> List[str]:
    if not results:
        return []
    industry_line, group_line = build_theme_rotation_summary(results)
    return [
        "【今日分布】",
        f"產業：{industry_line}",
        f"族群：{group_line}",
        "",
    ]
