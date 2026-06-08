"""sector_summary 單元測試。"""

from __future__ import annotations

import pandas as pd

from src.data.stock_metadata import StockMetadata
from src.screener.conditions import ScreenResult
from src.screener.grading import GradedScreenResult
from src.screener.sector_summary import build_rotation_summary, format_rotation_block


def _graded(code: str, grade: str = "A") -> GradedScreenResult:
    result = ScreenResult(
        stock_code=code,
        signal_date=pd.Timestamp("2024-06-01"),
        close=100.0,
        gain_pct=15.0,
        retest_ma="ma5",
        golden_cross_date=pd.Timestamp("2024-05-28"),
        death_cross_date=pd.Timestamp("2024-05-20"),
        oscillation_bars=4,
        ma20=98.0,
        ma60=95.0,
        ma120=90.0,
        volume=600_000,
    )
    return GradedScreenResult(
        result=result,
        grade=grade,  # type: ignore[arg-type]
        volume_ratio=1.2,
        retest_touch_pct=0.3,
        dist_to_high_pct=5.0,
        review_notes=["test"],
    )


def test_build_rotation_summary():
    metadata = {
        "2330": StockMetadata(industry="半導體業", groups=("晶圓代工",)),
        "2454": StockMetadata(industry="半導體業", groups=("IC設計",)),
        "2618": StockMetadata(industry="航運業", groups=()),
    }
    results = [_graded("2330"), _graded("2454"), _graded("2618", "B")]
    industry_line, group_line = build_rotation_summary(results, metadata)
    assert "半導體業(2)" in industry_line
    assert "航運業(1)" in industry_line
    assert "晶圓代工(1)" in group_line
    assert "IC設計(1)" in group_line


def test_format_rotation_block():
    metadata = {"2330": StockMetadata(industry="半導體業", groups=("晶圓代工",))}
    lines = format_rotation_block([_graded("2330")], metadata)
    assert lines[0] == "【今日分布】"
    assert lines[1].startswith("產業：")
