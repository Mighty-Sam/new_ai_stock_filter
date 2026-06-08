"""stock_metadata 單元測試。"""

from __future__ import annotations

from unittest.mock import patch

from src.data.stock_metadata import (
    StockMetadata,
    fetch_all_metadata,
    lookup_metadata,
    merge_metadata,
)


def test_merge_metadata_dedup_groups():
    industries = {"2330": "半導體業", "2317": "其他電子業"}
    merged = merge_metadata(industries, {"2330": ("晶圓代工", "先進封裝"), "2317": ()})
    assert merged["2330"].industry == "半導體業"
    assert merged["2330"].groups == ("晶圓代工", "先進封裝")
    assert merged["2317"].groups == ()


def test_lookup_metadata_fallback():
    meta = lookup_metadata({}, "9999")
    assert meta.industry == "—"
    assert meta.groups == ()


def test_groups_display_truncated():
    meta = StockMetadata(industry="半導體業", groups=("A" * 20, "B" * 20))
    text = meta.groups_display_truncated(40)
    assert len(text) <= 40
    assert text.endswith("…")


@patch("src.data.stock_metadata.fetch_industry_groups")
@patch("src.data.stock_metadata.fetch_industry_categories")
def test_fetch_all_metadata(mock_ind, mock_grp):
    mock_ind.return_value = {"2330": "半導體業"}
    mock_grp.return_value = {"2330": ("晶圓代工",)}
    data = fetch_all_metadata()
    assert data["2330"].groups == ("晶圓代工",)
