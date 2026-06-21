"""策略一優化推播篩選（2026-06-01 固定持有回測衍生）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from src.screener.grading import GradedScreenResult

# 2026-06-01 信號 → 6/2 開買 → 6/18 收賣回測結論：
# - A 級 2 檔勝率 100%、均報酬 +12.6%；B 級 68 檔均報酬 -2.2%
# - 跌 ≤-10% 有 22 檔，多為 B + 量能偏弱 + 20K 漲幅過高
# - MA5 子樣本優於 MA10；MA10 需更高量能門檻
MAX_SIGNAL_GAIN_PCT = 30.0
MIN_VOLUME_RATIO_B = 1.0
MIN_VOLUME_RATIO_MA10 = 1.2
MIN_DIST_TO_HIGH_PCT = 3.0


@dataclass(frozen=True)
class OptimizedFilterConfig:
    max_signal_gain_pct: float = MAX_SIGNAL_GAIN_PCT
    min_volume_ratio_b: float = MIN_VOLUME_RATIO_B
    min_volume_ratio_ma10: float = MIN_VOLUME_RATIO_MA10
    min_dist_to_high_pct: float = MIN_DIST_TO_HIGH_PCT


DEFAULT_OPTIMIZED_CONFIG = OptimizedFilterConfig()


def passes_optimized_push(
    graded: GradedScreenResult,
    config: OptimizedFilterConfig = DEFAULT_OPTIMIZED_CONFIG,
) -> bool:
    """A 級全收；B 級需通過量能、漲幅、距高點、均線門檻。"""
    if graded.grade == "A":
        return True

    if graded.gain_pct > config.max_signal_gain_pct:
        return False
    if graded.dist_to_high_pct < config.min_dist_to_high_pct:
        return False
    if graded.volume_ratio < config.min_volume_ratio_b:
        return False
    if graded.retest_ma == "ma10" and graded.volume_ratio < config.min_volume_ratio_ma10:
        return False
    return True


def filter_optimized(
    results: List[GradedScreenResult],
    config: OptimizedFilterConfig = DEFAULT_OPTIMIZED_CONFIG,
) -> List[GradedScreenResult]:
    """保留通過優化篩選的結果（維持原排序）。"""
    return [r for r in results if passes_optimized_push(r, config)]
