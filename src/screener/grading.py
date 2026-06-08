"""v1/v2 品質分級與人工確認提示。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal

import pandas as pd

from src.screener.conditions import ScreenResult, evaluate_with_params
from src.screener.params import V2_BASE_PARAMS

Grade = Literal["A", "B"]


@dataclass
class GradedScreenResult:
    """v1 信號 + 品質分級 + 人工確認提示。"""

    result: ScreenResult
    grade: Grade
    volume_ratio: float
    retest_touch_pct: float
    dist_to_high_pct: float
    review_notes: List[str] = field(default_factory=list)

    @property
    def stock_code(self) -> str:
        return self.result.stock_code

    @property
    def gain_pct(self) -> float:
        return self.result.gain_pct

    @property
    def signal_date(self) -> pd.Timestamp:
        return self.result.signal_date

    @property
    def retest_ma(self) -> str:
        return self.result.retest_ma


def _volume_ratio(df: pd.DataFrame, signal_idx: int, period: int = 5) -> float:
    if signal_idx < period - 1:
        return 0.0
    window = df.iloc[signal_idx - period + 1 : signal_idx + 1]["volume"]
    avg = window.mean()
    if pd.isna(avg) or avg <= 0:
        return 0.0
    return round(float(df.iloc[signal_idx]["volume"]) / float(avg), 2)


def _retest_touch_pct(row: pd.Series, retest_ma: str) -> float:
    ma_val = float(row[retest_ma])
    if ma_val <= 0:
        return 999.0
    return round(abs(float(row["low"]) - ma_val) / ma_val * 100, 2)


def _dist_to_high_pct(df: pd.DataFrame, signal_idx: int, lookback: int = 20) -> float:
    start = max(0, signal_idx - lookback + 1)
    window = df.iloc[start : signal_idx + 1]
    high_20 = float(window["high"].max())
    close = float(df.iloc[signal_idx]["close"])
    if high_20 <= 0:
        return 0.0
    return round((high_20 - close) / high_20 * 100, 2)


def build_review_notes(
    retest_touch_pct: float,
    retest_ma: str,
    volume_ratio: float,
    dist_to_high_pct: float,
) -> List[str]:
    ma_label = "MA5" if retest_ma == "ma5" else "MA10"
    notes: List[str] = []

    if retest_touch_pct <= 0.5:
        notes.append(f"✅ 回踩貼近（低點距 {ma_label} {retest_touch_pct:.1f}%）")
    elif retest_touch_pct <= 1.0:
        notes.append(
            f"⚠️ 回踩略寬（低點距 {ma_label} {retest_touch_pct:.1f}%）— 請確認是否有效支撐"
        )
    else:
        notes.append(f"⚠️ 回踩偏離（{retest_touch_pct:.1f}%）— 建議人工看圖確認")

    if volume_ratio >= 1.2:
        notes.append(f"✅ 量增健康（{volume_ratio:.2f}× 5日均量）")
    elif volume_ratio >= 1.0:
        notes.append(f"⚠️ 量能尚可（{volume_ratio:.2f}×）— 需確認是否有效放量")
    else:
        notes.append(f"⚠️ 量能偏弱（{volume_ratio:.2f}×）— 建議觀望或小倉")

    if dist_to_high_pct <= 2:
        notes.append(f"⚠️ 接近 20 日高點（距壓力 {dist_to_high_pct:.1f}%）— 留意回檔")
    elif dist_to_high_pct <= 5:
        notes.append(f"ℹ️ 距 20 日高 {dist_to_high_pct:.1f}%")
    else:
        notes.append(f"✅ 上方空間較足（距 20 日高 {dist_to_high_pct:.1f}%）")

    return notes


def grade_screen_result(df: pd.DataFrame, v1_result: ScreenResult) -> GradedScreenResult:
    """v1 已通過；若同時符合 v2 則為 A 級，否則 B 級。"""
    signal_idx = len(df) - 1
    row = df.iloc[signal_idx]

    v2_result = evaluate_with_params(df, v1_result.stock_code, V2_BASE_PARAMS)
    grade: Grade = "A" if v2_result is not None else "B"

    vol_ratio = _volume_ratio(df, signal_idx)
    touch_pct = _retest_touch_pct(row, v1_result.retest_ma)
    dist_high = _dist_to_high_pct(df, signal_idx)
    notes = build_review_notes(touch_pct, v1_result.retest_ma, vol_ratio, dist_high)

    if grade == "A":
        notes.insert(0, "⭐ A 級：符合 v2 嚴選（優先觀察、可加大部位）")
    else:
        notes.insert(0, "B 級：僅 v1 條件（次級、小倉或略過）")

    return GradedScreenResult(
        result=v1_result,
        grade=grade,
        volume_ratio=vol_ratio,
        retest_touch_pct=touch_pct,
        dist_to_high_pct=dist_high,
        review_notes=notes,
    )


def sort_graded_results(results: List[GradedScreenResult]) -> List[GradedScreenResult]:
    return sorted(results, key=lambda r: (0 if r.grade == "A" else 1, -r.gain_pct))
