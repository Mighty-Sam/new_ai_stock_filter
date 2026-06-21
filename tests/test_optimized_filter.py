"""optimized_filter 單元測試。"""

from __future__ import annotations

import pandas as pd

from src.screener.conditions import ScreenResult
from src.screener.grading import GradedScreenResult
from src.screener.optimized_filter import filter_optimized, passes_optimized_push


def _graded(
    grade: str = "B",
    gain_pct: float = 15.0,
    volume_ratio: float = 1.5,
    retest_ma: str = "ma5",
    dist_to_high_pct: float = 5.0,
) -> GradedScreenResult:
    return GradedScreenResult(
        result=ScreenResult(
            stock_code="1234",
            signal_date=pd.Timestamp("2026-06-01"),
            close=50.0,
            gain_pct=gain_pct,
            retest_ma=retest_ma,  # type: ignore[arg-type]
            golden_cross_date=pd.Timestamp("2026-05-20"),
            death_cross_date=pd.Timestamp("2026-05-10"),
            oscillation_bars=5,
            ma20=48.0,
            ma60=45.0,
            ma120=40.0,
            volume=1_000_000,
        ),
        grade=grade,  # type: ignore[arg-type]
        volume_ratio=volume_ratio,
        retest_touch_pct=0.5,
        dist_to_high_pct=dist_to_high_pct,
    )


def test_a_grade_always_passes():
    g = _graded(grade="A", volume_ratio=0.5, gain_pct=35.0, dist_to_high_pct=1.0)
    assert passes_optimized_push(g) is True


def test_b_rejects_high_gain():
    g = _graded(gain_pct=31.0)
    assert passes_optimized_push(g) is False


def test_b_rejects_weak_volume():
    g = _graded(volume_ratio=0.8)
    assert passes_optimized_push(g) is False


def test_b_ma10_needs_higher_volume():
    g = _graded(retest_ma="ma10", volume_ratio=1.1)
    assert passes_optimized_push(g) is False
    g2 = _graded(retest_ma="ma10", volume_ratio=1.2)
    assert passes_optimized_push(g2) is True


def test_b_rejects_near_high():
    g = _graded(dist_to_high_pct=2.0)
    assert passes_optimized_push(g) is False


def test_filter_optimized_drops_ineligible():
    a = _graded(grade="A")
    b = _graded(gain_pct=20.0)
    bad = _graded(gain_pct=40.0)
    out = filter_optimized([bad, b, a])
    assert len(out) == 2
    codes = {r.grade for r in out}
    assert codes == {"A", "B"}
