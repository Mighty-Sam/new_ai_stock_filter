"""Telegram Bot API 推播。"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from src.backtest.stats import BacktestSummary, format_period_line
from src.backtest.tracker import EXIT_REASON_LABELS, SettledTrade
from src.backtest.trade_simulator import STRATEGY_LABEL
from src.data.stock_metadata import StockMetadata, lookup_metadata
from src.screener.grading import GradedScreenResult
from src.screener.sector_summary import format_rotation_block, format_theme_rotation_block
from src.screener.theme_conditions import ThemeScreenResult

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    @property
    def _base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    def send_message(self, text: str, parse_mode: Optional[str] = None) -> bool:
        if not self.configured:
            logger.warning("Telegram 未設定，略過推播")
            return False

        payload: dict = {"chat_id": self.chat_id, "text": text[:4096]}
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            response = requests.post(
                f"{self._base_url}/sendMessage",
                json=payload,
                timeout=30,
            )
            if response.status_code != 200:
                logger.error("Telegram sendMessage 失敗: %s", response.text)
                return False
            return response.json().get("ok", False)
        except Exception as exc:
            logger.error("Telegram sendMessage 例外: %s", exc)
            return False

    def send_photo(self, image_path: Path, caption: str = "") -> bool:
        if not self.configured:
            logger.warning("Telegram 未設定，略過推播")
            return False

        try:
            with image_path.open("rb") as photo:
                data = {"chat_id": self.chat_id}
                if caption:
                    data["caption"] = caption[:1024]
                response = requests.post(
                    f"{self._base_url}/sendPhoto",
                    data=data,
                    files={"photo": photo},
                    timeout=60,
                )
            if response.status_code != 200:
                logger.error("Telegram sendPhoto 失敗: %s", response.text)
                return False
            return response.json().get("ok", False)
        except Exception as exc:
            logger.error("Telegram sendPhoto 例外: %s", exc)
            return False

    def _format_industry_line(self, stock_code: str, metadata: Dict[str, StockMetadata]) -> str:
        meta = lookup_metadata(metadata, stock_code)
        return f"   產業：{meta.industry} | 族群：{meta.groups_display}"

    def _format_industry_caption(
        self,
        stock_code: str,
        metadata: Dict[str, StockMetadata],
    ) -> str:
        meta = lookup_metadata(metadata, stock_code)
        return f"產業：{meta.industry} | 族群：{meta.groups_display_truncated(40)}"

    def _format_graded_line(
        self,
        graded: GradedScreenResult,
        index: int,
        stock_names: Dict[str, str],
        metadata: Dict[str, StockMetadata],
    ) -> str:
        r = graded.result
        name = stock_names.get(r.stock_code, "")
        ma_label = "MA5" if r.retest_ma == "ma5" else "MA10"
        grade_icon = "⭐" if graded.grade == "A" else "○"
        lines = [
            f"{index}. {grade_icon} {r.stock_code} {name}",
            f"   [{graded.grade}級] 收盤 {r.close:.2f} | 20K漲幅 {r.gain_pct}%",
            self._format_industry_line(r.stock_code, metadata),
            f"   回踩 {ma_label} | 整理 {r.oscillation_bars} 根 | 量比 {graded.volume_ratio:.2f}×",
        ]
        for note in graded.review_notes[1:4]:
            lines.append(f"   {note}")
        return "\n".join(lines)

    def format_summary(
        self,
        results: List[GradedScreenResult],
        stock_names: Dict[str, str],
        scan_date: str,
        metadata: Optional[Dict[str, StockMetadata]] = None,
        v1_total: int = 0,
    ) -> str:
        meta = metadata or {}
        grade_a = [r for r in results if r.grade == "A"]
        grade_b = [r for r in results if r.grade == "B"]
        count_line = (
            f"v1 符合 {v1_total} 檔 → 優化後 {len(results)} 檔"
            f"（A 級 {len(grade_a)} / B 級 {len(grade_b)}）"
            if v1_total > 0
            else f"符合：{len(results)} 檔（A 級 {len(grade_a)} / B 級 {len(grade_b)}）"
        )

        if not results:
            return (
                f"📊 台股均線回踩選股（優化版）\n"
                f"日期：{scan_date}\n"
                f"{count_line}\n\n"
                f"今日無符合條件個股。"
            )

        lines = [
            "📊 台股均線回踩選股（優化版）",
            f"日期：{scan_date}",
            count_line,
            "",
        ]
        lines.extend(format_rotation_block(results, meta))

        if grade_a:
            lines.append("【A 級 — 優先觀察】")
            for i, g in enumerate(grade_a[:10], 1):
                lines.append(self._format_graded_line(g, i, stock_names, meta))
            if len(grade_a) > 10:
                lines.append(f"... 其餘 A 級 {len(grade_a) - 10} 檔")
            lines.append("")

        if grade_b:
            lines.append("【B 級 — 次級參考】")
            for i, g in enumerate(grade_b[:10], 1):
                lines.append(self._format_graded_line(g, i, stock_names, meta))
            if len(grade_b) > 10:
                lines.append(f"... 其餘 B 級 {len(grade_b) - 10} 檔")

        return "\n".join(lines)

    def format_forward_backtest(
        self,
        scan_date: str,
        forward_summary: Optional[BacktestSummary],
        today_settled: List[SettledTrade],
        pending_count: int = 0,
    ) -> str:
        lines = [
            "📈 均線回踩 — 前瞻回測（優化版）",
            f"日期：{scan_date}",
            f"規則：{STRATEGY_LABEL}",
            "",
            "--- 累計 ---",
        ]
        if forward_summary and forward_summary.period_stats:
            for ps in forward_summary.period_stats:
                lines.append(format_period_line(ps))
        else:
            lines.append("尚無已結算資料")

        if pending_count > 0:
            lines.append(f"追蹤中：{pending_count} 檔尚未結算")

        lines.append("")
        lines.append("--- 今日結算 ---")
        if today_settled:
            for i, t in enumerate(today_settled, 1):
                reason = EXIT_REASON_LABELS.get(t.exit_reason, t.exit_reason)
                sign = "+" if t.return_pct >= 0 else ""
                lines.append(
                    f"{i}. {t.stock_code} {reason} {sign}{t.return_pct:.1f}%"
                    f"（持有 {t.hold_days} 日）"
                )
        else:
            lines.append("今日無新結算")

        return "\n".join(lines)

    def notify_scan_results(
        self,
        results: List[GradedScreenResult],
        stock_names: Dict[str, str],
        chart_paths: Dict[str, Path],
        scan_date: str,
        metadata: Optional[Dict[str, StockMetadata]] = None,
        batch_delay: float = 1.0,
        v1_total: int = 0,
    ) -> None:
        if not self.configured:
            logger.warning("Telegram 未設定")
            return

        meta = metadata or {}
        summary = self.format_summary(
            results,
            stock_names,
            scan_date,
            metadata=meta,
            v1_total=v1_total,
        )
        self.send_message(summary)
        time.sleep(batch_delay)

        for g in results:
            path = chart_paths.get(g.stock_code)
            if path and path.exists():
                r = g.result
                name = stock_names.get(g.stock_code, g.stock_code)
                ma_label = "MA5" if r.retest_ma == "ma5" else "MA10"
                caption_lines = [
                    f"📈 [{g.grade}級] {g.stock_code} {name}",
                    f"收盤 {r.close:.2f} | 漲幅 {r.gain_pct}%",
                    self._format_industry_caption(g.stock_code, meta),
                    f"回踩 {ma_label} | 量比 {g.volume_ratio:.2f}×",
                ]
                caption_lines.extend(g.review_notes[1:3])
                self.send_photo(path, caption="\n".join(caption_lines))
                time.sleep(batch_delay)

    def notify_forward_backtest(
        self,
        scan_date: str,
        forward_summary: Optional[BacktestSummary],
        today_settled: List[SettledTrade],
        pending_count: int = 0,
        batch_delay: float = 1.0,
    ) -> None:
        if not self.configured:
            logger.warning("Telegram 未設定")
            return

        summary = self.format_forward_backtest(
            scan_date=scan_date,
            forward_summary=forward_summary,
            today_settled=today_settled,
            pending_count=pending_count,
        )
        self.send_message(summary)
        time.sleep(batch_delay)

    def _format_theme_line(
        self,
        result: ThemeScreenResult,
        index: int,
        stock_names: Dict[str, str],
    ) -> str:
        name = stock_names.get(result.stock_code, "")
        groups = "、".join(result.groups) if result.groups else "—"
        lines = [
            f"{index}. 🔥 {result.stock_code} {name}",
            f"   收盤 {result.close:.2f} | 20日漲幅 {result.gain_20d_pct}%",
            f"   市值 {result.market_cap_billions:.1f}億 | 董監 {result.director_holding_pct:.1f}%",
            f"   產業：{result.industry} | 族群：{groups}",
            f"   量比 {result.volume_ratio:.2f}× | 突破 {result.high_20d:.2f}",
        ]
        for note in result.review_notes[-2:]:
            lines.append(f"   {note}")
        return "\n".join(lines)

    def format_theme_summary(
        self,
        results: List[ThemeScreenResult],
        stock_names: Dict[str, str],
        scan_date: str,
        hot_industries: Optional[List[str]] = None,
        stage1_count: int = 0,
    ) -> str:
        if not results:
            hot_line = ""
            if hot_industries:
                hot_line = f"\n熱門產業：{'、'.join(hot_industries)}"
            stage_line = f"（第一階段候選 {stage1_count} 檔）" if stage1_count else ""
            return (
                f"🔥 低位題材動能選股\n日期：{scan_date}\n\n"
                f"今日無符合條件個股。{stage_line}{hot_line}"
            )

        lines = [
            "🔥 低位題材動能選股",
            f"日期：{scan_date}",
            f"符合：{len(results)} 檔（第一階段 {stage1_count} 檔）",
            "",
        ]
        if hot_industries:
            lines.append(f"熱門產業：{'、'.join(hot_industries)}")
            lines.append("")
        lines.extend(format_theme_rotation_block(results))

        for i, r in enumerate(results[:15], 1):
            lines.append(self._format_theme_line(r, i, stock_names))
        if len(results) > 15:
            lines.append(f"... 其餘 {len(results) - 15} 檔")

        return "\n".join(lines)

    def notify_theme_results(
        self,
        results: List[ThemeScreenResult],
        stock_names: Dict[str, str],
        chart_paths: Dict[str, Path],
        scan_date: str,
        hot_industries: Optional[List[str]] = None,
        stage1_count: int = 0,
        batch_delay: float = 1.0,
    ) -> None:
        if not self.configured:
            logger.warning("Telegram 未設定")
            return

        summary = self.format_theme_summary(
            results,
            stock_names,
            scan_date,
            hot_industries=hot_industries,
            stage1_count=stage1_count,
        )
        self.send_message(summary)
        time.sleep(batch_delay)

        for r in results:
            path = chart_paths.get(r.stock_code)
            if path and path.exists():
                name = stock_names.get(r.stock_code, r.stock_code)
                groups = "、".join(r.groups) if r.groups else "—"
                caption = "\n".join(
                    [
                        f"🔥 {r.stock_code} {name}",
                        f"收盤 {r.close:.2f} | 20日漲幅 {r.gain_20d_pct}%",
                        f"市值 {r.market_cap_billions:.1f}億 | 董監 {r.director_holding_pct:.1f}%",
                        f"產業：{r.industry} | 族群：{groups}",
                        f"量比 {r.volume_ratio:.2f}×",
                    ]
                )
                self.send_photo(path, caption=caption)
                time.sleep(batch_delay)
