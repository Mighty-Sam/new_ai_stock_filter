"""低位題材動能選股條件判定。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from src.data.stock_metadata import MISSING, StockMetadata
from src.screener.theme_params import (
    HOT_INDUSTRY_COUNT_WEIGHT,
    HOT_INDUSTRY_TOP_N,
    MAX_MARKET_CAP_BILLION,
    MAX_PRICE,
    MIN_DIRECTOR_HOLDING_PCT,
    MIN_GAIN_20D_PCT,
    VOLUME_BREAKOUT_RATIO,
    VOLUME_MA_DAYS,
)


@dataclass
class ThemeScreenResult:
    stock_code: str
    signal_date: pd.Timestamp
    close: float
    gain_20d_pct: float
    volume_ratio: float
    market_cap_billions: float
    director_holding_pct: float
    industry: str
    groups: tuple[str, ...]
    high_20d: float
    review_notes: List[str] = field(default_factory=list)


def _industry_label(meta: Optional[StockMetadata]) -> str:
    if meta is None or meta.industry == MISSING:
        return "未知"
    return meta.industry


def evaluate_theme_candidate(
    df: pd.DataFrame,
    stock_code: str,
    market_cap_billions: Optional[float],
    director_holding_pct: Optional[float],
    metadata: Optional[StockMetadata] = None,
) -> Optional[ThemeScreenResult]:
    """
    第一階段：單檔技術 + 基本面門檻（不含動態熱門產業）。
    """
    if df is None or df.empty or len(df) < VOLUME_MA_DAYS + 1:
        return None
    if market_cap_billions is None or director_holding_pct is None:
        return None

    df = df.sort_index()
    row = df.iloc[-1]
    close = float(row["close"])
    volume = float(row["volume"])

    notes: List[str] = []

    if close > MAX_PRICE:
        return None
    notes.append(f"低位：收盤 {close:.2f} ≤ {MAX_PRICE:.0f}")

    if market_cap_billions >= MAX_MARKET_CAP_BILLION:
        return None
    notes.append(f"小市值：{market_cap_billions:.1f} 億 < {MAX_MARKET_CAP_BILLION:.0f} 億")

    if director_holding_pct < MIN_DIRECTOR_HOLDING_PCT:
        return None
    notes.append(f"籌碼：董監持股 {director_holding_pct:.1f}% ≥ {MIN_DIRECTOR_HOLDING_PCT:.0f}%")

    close_20d_ago = float(df.iloc[-1 - VOLUME_MA_DAYS]["close"])
    if close_20d_ago <= 0:
        return None
    gain_20d_pct = (close - close_20d_ago) / close_20d_ago * 100
    if gain_20d_pct < MIN_GAIN_20D_PCT:
        return None
    notes.append(f"動能：20日漲幅 {gain_20d_pct:.1f}% ≥ {MIN_GAIN_20D_PCT:.0f}%")

    vol_ma = float(df["volume"].tail(VOLUME_MA_DAYS).mean())
    if vol_ma <= 0:
        return None
    volume_ratio = volume / vol_ma
    if volume_ratio < VOLUME_BREAKOUT_RATIO:
        return None

    window = df.tail(VOLUME_MA_DAYS)
    high_20d = float(window["high"].max())
    if close < high_20d:
        return None
    notes.append(
        f"突破：量比 {volume_ratio:.2f}×、收盤突破 {VOLUME_MA_DAYS} 日高 {high_20d:.2f}"
    )

    industry = _industry_label(metadata)
    groups = metadata.groups if metadata else ()

    return ThemeScreenResult(
        stock_code=stock_code,
        signal_date=df.index[-1],
        close=round(close, 2),
        gain_20d_pct=round(gain_20d_pct, 2),
        volume_ratio=round(volume_ratio, 2),
        market_cap_billions=round(market_cap_billions, 2),
        director_holding_pct=round(director_holding_pct, 2),
        industry=industry,
        groups=groups,
        high_20d=round(high_20d, 2),
        review_notes=notes,
    )


def filter_by_hot_industries(
    candidates: List[ThemeScreenResult],
    top_n: int = HOT_INDUSTRY_TOP_N,
) -> tuple[List[ThemeScreenResult], List[str]]:
    """
    第二階段：依產業候選密度 + 平均漲幅排名，只保留熱門產業內個股。
    回傳 (過濾後清單, 熱門產業名稱列表)。
    """
    if not candidates:
        return [], []

    by_industry: Dict[str, List[ThemeScreenResult]] = defaultdict(list)
    for c in candidates:
        by_industry[c.industry].append(c)

    scores: List[tuple[str, float]] = []
    for industry, items in by_industry.items():
        if industry == "未知":
            continue
        avg_gain = sum(i.gain_20d_pct for i in items) / len(items)
        score = len(items) * HOT_INDUSTRY_COUNT_WEIGHT + avg_gain
        scores.append((industry, score))

    scores.sort(key=lambda x: (-x[1], x[0]))
    hot = [name for name, _ in scores[:top_n]]
    if not hot:
        return [], []

    filtered = [c for c in candidates if c.industry in hot]
    for c in filtered:
        c.review_notes.append(f"熱門產業：{c.industry}")

    filtered.sort(key=lambda x: (-x.gain_20d_pct, x.stock_code))
    return filtered, hot


def sort_theme_results(results: List[ThemeScreenResult]) -> List[ThemeScreenResult]:
    return sorted(results, key=lambda x: (-x.gain_20d_pct, x.stock_code))
