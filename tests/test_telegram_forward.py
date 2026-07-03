"""Telegram 前瞻回測推播格式測試。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.backtest.stats import BacktestSummary, PeriodStats
from src.backtest.tracker import MaturityCohortReport, SettledTrade
from src.notify.telegram_client import TelegramClient
from src.screener.conditions import ScreenResult
from src.screener.grading import GradedScreenResult


def test_format_summary_optimized_with_v1_total():
    client = TelegramClient(bot_token="x", chat_id="y")
    text = client.format_summary([], {}, "2026/06/05", v1_total=70)
    assert "優化版" in text
    assert "v1 符合 70 檔 → 優化後 0 檔" in text
    assert "近3年" not in text
    assert "前瞻追蹤" not in text


def test_format_summary_grade_a_only():
    client = TelegramClient(bot_token="x", chat_id="y")
    text = client.format_summary([], {}, "2026/06/05", v1_total=70, grade_a_only=True)
    assert "A 級" in text
    assert "v1 符合 70 檔 → A 級 0 檔" in text
    assert "B 級" not in text


def test_format_forward_backtest_cohort_with_trades():
    client = TelegramClient(bot_token="x", chat_id="y")
    summary = BacktestSummary(
        source="forward",
        period_stats=[
            PeriodStats(
                hold_days=20,
                label="停損-10%/停利+30%（最多20日）",
                sample_count=2,
                win_rate=100.0,
                avg_return_pct=12.6,
                beat_benchmark_rate=50.0,
            )
        ],
    )
    cohort = MaturityCohortReport(
        scan_date=date(2026, 6, 29),
        signal_date=date(2026, 6, 1),
        trades=[
            SettledTrade(
                stock_code="1904",
                signal_date=date(2026, 6, 1),
                entry_date=date(2026, 6, 3),
                entry_price=50.0,
                exit_date=date(2026, 6, 10),
                exit_price=65.0,
                return_pct=30.0,
                exit_reason="take_profit",
                hold_days=5,
            ),
            SettledTrade(
                stock_code="4721",
                signal_date=date(2026, 6, 1),
                entry_date=date(2026, 6, 3),
                entry_price=100.0,
                exit_date=date(2026, 6, 29),
                exit_price=108.2,
                return_pct=8.2,
                exit_reason="timeout",
                hold_days=20,
            ),
        ],
        summary=summary,
    )
    text = client.format_forward_backtest(scan_date="2026/06/29", cohort=cohort)
    assert "前瞻回測（A 級批次）" in text
    assert "信號日：2026/06/01" in text
    assert "勝率 100.0%" in text
    assert "1904 停利 +30.0%" in text
    assert "4721 到期 +8.2%" in text
    assert "買 6/3 50.00" in text
    assert "累計" not in text
    assert "今日結算" not in text


def test_format_forward_backtest_warmup():
    client = TelegramClient(bot_token="x", chat_id="y")
    cohort = MaturityCohortReport(scan_date=date(2026, 6, 5), signal_date=None)
    text = client.format_forward_backtest(scan_date="2026/06/05", cohort=cohort)
    assert "尚無可回報批次" in text


def test_format_forward_backtest_no_picks():
    client = TelegramClient(bot_token="x", chat_id="y")
    cohort = MaturityCohortReport(
        scan_date=date(2026, 6, 29),
        signal_date=date(2026, 6, 1),
        trades=[],
    )
    text = client.format_forward_backtest(scan_date="2026/06/29", cohort=cohort)
    assert "該信號日無 A 級選股" in text


def test_format_summary_includes_chip_line():
    from src.data.institutional import InstitutionalFlow

    client = TelegramClient(bot_token="x", chat_id="y")
    flow = InstitutionalFlow(
        stock_code="2330",
        as_of_date=date(2026, 6, 20),
        foreign_net_lots=100.0,
        trust_net_lots=20.0,
        dealer_net_lots=5.0,
        total_net_lots=125.0,
        sum_5d_net_lots=200.0,
        consecutive_buy_days=1,
    )
    graded = GradedScreenResult(
        result=ScreenResult(
            stock_code="2330",
            signal_date=pd.Timestamp("2026-06-20"),
            close=100.0,
            gain_pct=18.0,
            retest_ma="ma5",
            golden_cross_date=pd.Timestamp("2026-06-15"),
            death_cross_date=pd.Timestamp("2026-06-01"),
            oscillation_bars=4,
            ma20=98.0,
            ma60=95.0,
            ma120=90.0,
            volume=600_000,
        ),
        grade="A",
        volume_ratio=1.2,
        retest_touch_pct=0.5,
        dist_to_high_pct=5.0,
        a_source="v2",
        review_notes=["⭐ A 級：v2 嚴選", "note"],
    )
    text = client.format_summary(
        [graded],
        {"2330": "台積電"},
        "2026/06/20",
        chip_flows={"2330": flow},
    )
    assert "籌碼(06/20)" in text
    assert "外資+100張↑" in text
    assert "[v2嚴選] 2330" in text
    assert "⭐ A 級：v2 嚴選" in text
