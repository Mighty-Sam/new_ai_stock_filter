"""三大法人籌碼單元測試。"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from src.data.institutional import (
    InstitutionalFlow,
    compute_institutional_flow,
    format_chip_line,
    get_institutional_flow,
)


def _sample_wide_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-06-18",
                "stock_id": "2330",
                "Foreign_Investor_buy": 1_000_000,
                "Foreign_Investor_sell": 500_000,
                "Foreign_Dealer_Self_buy": 0,
                "Foreign_Dealer_Self_sell": 0,
                "Investment_Trust_buy": 100_000,
                "Investment_Trust_sell": 50_000,
                "Dealer_buy": 0,
                "Dealer_sell": 0,
                "Dealer_self_buy": 80_000,
                "Dealer_self_sell": 100_000,
                "Dealer_Hedging_buy": 0,
                "Dealer_Hedging_sell": 0,
            },
            {
                "date": "2026-06-19",
                "stock_id": "2330",
                "Foreign_Investor_buy": 800_000,
                "Foreign_Investor_sell": 200_000,
                "Foreign_Dealer_Self_buy": 0,
                "Foreign_Dealer_Self_sell": 0,
                "Investment_Trust_buy": 50_000,
                "Investment_Trust_sell": 30_000,
                "Dealer_buy": 0,
                "Dealer_sell": 0,
                "Dealer_self_buy": 40_000,
                "Dealer_self_sell": 20_000,
                "Dealer_Hedging_buy": 0,
                "Dealer_Hedging_sell": 0,
            },
            {
                "date": "2026-06-20",
                "stock_id": "2330",
                "Foreign_Investor_buy": 300_000,
                "Foreign_Investor_sell": 400_000,
                "Foreign_Dealer_Self_buy": 0,
                "Foreign_Dealer_Self_sell": 0,
                "Investment_Trust_buy": 20_000,
                "Investment_Trust_sell": 10_000,
                "Dealer_buy": 0,
                "Dealer_sell": 0,
                "Dealer_self_buy": 10_000,
                "Dealer_self_sell": 5_000,
                "Dealer_Hedging_buy": 0,
                "Dealer_Hedging_sell": 0,
            },
        ]
    )


def test_compute_institutional_flow_net_and_streak():
    flow = compute_institutional_flow(_sample_wide_rows(), "2330")
    assert flow is not None
    assert flow.as_of_date == date(2026, 6, 20)
    assert flow.foreign_net_lots == -100.0
    assert flow.trust_net_lots == 10.0
    assert flow.dealer_net_lots == 5.0
    assert flow.total_net_lots == -85.0
    assert flow.consecutive_buy_days == 0


def test_compute_institutional_flow_consecutive_buy_days():
    rows = _sample_wide_rows()
    rows.iloc[2, rows.columns.get_loc("Foreign_Investor_buy")] = 2_000_000
    rows.iloc[2, rows.columns.get_loc("Foreign_Investor_sell")] = 100_000
    flow = compute_institutional_flow(rows, "2330")
    assert flow is not None
    assert flow.total_net_lots > 0
    assert flow.consecutive_buy_days == 3
    assert flow.sum_5d_net_lots > 0


def test_format_chip_line():
    flow = InstitutionalFlow(
        stock_code="2330",
        as_of_date=date(2026, 6, 20),
        foreign_net_lots=320.0,
        trust_net_lots=50.0,
        dealer_net_lots=-20.0,
        total_net_lots=350.0,
        sum_5d_net_lots=350.0,
        consecutive_buy_days=2,
    )
    text = format_chip_line(flow)
    assert "籌碼(06/20)" in text
    assert "外資+320張↑" in text
    assert "近5日+350張↑" in text
    assert "連2日買超" in text


@patch("src.data.institutional.fetch_institutional_wide")
def test_get_institutional_flow_delegates(mock_fetch):
    mock_fetch.return_value = _sample_wide_rows()
    flow = get_institutional_flow("2330")
    assert flow is not None
    assert flow.stock_code == "2330"
