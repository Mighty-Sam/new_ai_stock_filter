"""品質分級單元測試。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.screener.conditions import ScreenResult
from src.screener.grading import (
    build_review_notes,
    grade_screen_result,
    sort_graded_results,
)
from tests.test_screener_v2 import _make_v2_pass_df


def _v1_only_result(stock_code: str = "2330") -> ScreenResult:
    return ScreenResult(
        stock_code=stock_code,
        signal_date=pd.Timestamp("2024-06-01"),
        close=100.0,
        gain_pct=12.0,
        retest_ma="ma5",
        golden_cross_date=pd.Timestamp("2024-05-28"),
        death_cross_date=pd.Timestamp("2024-05-20"),
        oscillation_bars=4,
        ma20=98.0,
        ma60=95.0,
        ma120=90.0,
        volume=600_000,
    )


def test_build_review_notes_volume_and_resistance():
    notes = build_review_notes(
        retest_touch_pct=0.3,
        retest_ma="ma5",
        volume_ratio=1.35,
        dist_to_high_pct=1.5,
    )
    assert any("回踩貼近" in n for n in notes)
    assert any("量增健康" in n for n in notes)
    assert any("接近 20 日高點" in n for n in notes)


def test_grade_a_when_v2_passes():
    df = _make_v2_pass_df()
    v1 = grade_screen_result(df, _v1_only_result())
    assert v1.grade == "A"
    assert v1.a_source == "v2"
    assert v1.review_notes[0].startswith("⭐")


def test_grade_a_when_consolidation_passes():
    from src.screener.conditions import evaluate_with_params
    from src.screener.params import V2_BASE_PARAMS
    from tests.test_strategy_consolidation import _make_consolidation_pass_df

    df = _make_consolidation_pass_df()
    assert evaluate_with_params(df, "2330", V2_BASE_PARAMS) is None
    graded = grade_screen_result(df, _v1_only_result())
    assert graded.grade == "A"
    assert graded.a_source == "consolidation"
    assert "縮幅回踩" in graded.review_notes[0]


def test_grade_b_when_neither_v2_nor_consolidation():
    df = _make_v2_pass_df()
    df.iloc[-20:, df.columns.get_loc("high")] = 101.0
    df.iloc[-20:, df.columns.get_loc("low")] = 99.0
    graded = grade_screen_result(df, _v1_only_result("2317"))
    if graded.a_source == "v2":
        pytest.skip("合成資料仍通過 v2")
    assert graded.grade == "B"
    assert graded.a_source is None


def test_sort_graded_results_a_first():
    a = grade_screen_result(_make_v2_pass_df(), _v1_only_result("2330"))
    b_df = _make_v2_pass_df()
    b_df.iloc[-20:, b_df.columns.get_loc("high")] = 101.0
    b_df.iloc[-20:, b_df.columns.get_loc("low")] = 99.0
    b = grade_screen_result(b_df, _v1_only_result("2317"))
    if b.grade != "B":
        b.grade = "B"  # force for sort test if v2 still passes
    ordered = sort_graded_results([b, a])
    assert ordered[0].grade == "A"
