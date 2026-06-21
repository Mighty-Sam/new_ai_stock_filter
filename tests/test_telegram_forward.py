"""Telegram 前瞻回測推播格式測試。"""

from __future__ import annotations

from datetime import date

from src.backtest.stats import BacktestSummary, PeriodStats
from src.backtest.tracker import SettledTrade
from src.notify.telegram_client import TelegramClient


def test_format_summary_optimized_with_v1_total():
    client = TelegramClient(bot_token="x", chat_id="y")
    text = client.format_summary([], {}, "2026/06/05", v1_total=70)
    assert "優化版" in text
    assert "v1 符合 70 檔 → 優化後 0 檔" in text
    assert "近3年" not in text
    assert "前瞻追蹤" not in text


def test_format_forward_backtest_with_today_settled():
    client = TelegramClient(bot_token="x", chat_id="y")
    summary = BacktestSummary(
        source="forward",
        period_stats=[
            PeriodStats(
                hold_days=20,
                label="停損-10%/停利+30%（最多20日）",
                sample_count=12,
                win_rate=58.3,
                avg_return_pct=1.48,
                beat_benchmark_rate=50.0,
            )
        ],
    )
    today = [
        SettledTrade(
            stock_code="2465",
            signal_date=date(2026, 5, 20),
            return_pct=31.2,
            exit_reason="take_profit",
            exit_date=date(2026, 6, 5),
            hold_days=8,
        ),
        SettledTrade(
            stock_code="3290",
            signal_date=date(2026, 6, 1),
            return_pct=-10.0,
            exit_reason="stop",
            exit_date=date(2026, 6, 5),
            hold_days=3,
        ),
    ]
    text = client.format_forward_backtest(
        scan_date="2026/06/05",
        forward_summary=summary,
        today_settled=today,
        pending_count=5,
    )
    assert "前瞻回測（優化版）" in text
    assert "勝率 58.3%" in text
    assert "追蹤中：5 檔尚未結算" in text
    assert "2465 停利 +31.2%" in text
    assert "3290 停損 -10.0%" in text


def test_format_forward_backtest_no_settlements():
    client = TelegramClient(bot_token="x", chat_id="y")
    text = client.format_forward_backtest(
        scan_date="2026/06/05",
        forward_summary=None,
        today_settled=[],
        pending_count=0,
    )
    assert "尚無已結算資料" in text
    assert "今日無新結算" in text
