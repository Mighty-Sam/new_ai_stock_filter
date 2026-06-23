"""Telegram 前瞻回測推播格式測試。"""

from __future__ import annotations

from datetime import date

from src.backtest.stats import BacktestSummary, PeriodStats
from src.backtest.tracker import MaturityCohortReport, SettledTrade
from src.notify.telegram_client import TelegramClient


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
