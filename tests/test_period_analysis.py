"""period_analysis 單元測試。"""

from datetime import date

import pandas as pd

from src.backtest.period_analysis import (
    ComboPreset,
    filter_combo,
    monthly_breakdown,
)


def test_filter_combo_v2():
    df = pd.DataFrame(
        [
            {
                "stock_code": "2330",
                "signal_date": pd.Timestamp("2022-03-01"),
                "stop_type": "pct_10",
                "tp_type": "pct_30",
                "min_oscillation": 3,
                "max_hold_days": 30,
                "entry_mode": "signal_close",
                "return_pct": 5.0,
                "is_win": True,
            },
            {
                "stock_code": "2330",
                "signal_date": pd.Timestamp("2022-03-01"),
                "stop_type": "pct_10",
                "tp_type": "pct_25",
                "min_oscillation": 3,
                "max_hold_days": 30,
                "entry_mode": "signal_close",
                "return_pct": -2.0,
                "is_win": False,
            },
        ]
    )
    preset = ComboPreset(
        label="test",
        strategy="v2",
        stop_type="pct_10",
        tp_type="pct_30",
        min_oscillation=3,
        max_hold_days=30,
        entry_mode="signal_close",
    )
    sub = filter_combo(df, preset)
    assert len(sub) == 1
    assert sub.iloc[0]["return_pct"] == 5.0


def test_monthly_breakdown():
    df = pd.DataFrame(
        [
            {
                "stock_code": "2330",
                "signal_date": pd.Timestamp("2022-01-10"),
                "return_pct": 10.0,
                "is_win": True,
            },
            {
                "stock_code": "2317",
                "signal_date": pd.Timestamp("2022-01-20"),
                "return_pct": -5.0,
                "is_win": False,
            },
            {
                "stock_code": "2454",
                "signal_date": pd.Timestamp("2022-02-05"),
                "return_pct": 3.0,
                "is_win": True,
            },
        ]
    )
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    months = monthly_breakdown(df)
    assert len(months) == 2
    jan = next(m for m in months if m.month == "2022-01")
    assert jan.trade_count == 2
    assert jan.signal_count == 2
    assert jan.win_rate == 50.0
    assert jan.avg_return_pct == 2.5
