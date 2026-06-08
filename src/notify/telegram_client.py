"""Telegram Bot API 推播。"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from src.backtest.stats import BacktestSummary, format_backtest_section
from src.data.stock_metadata import StockMetadata, lookup_metadata
from src.screener.grading import GradedScreenResult
from src.screener.sector_summary import format_rotation_block

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
        historical_summary: Optional[BacktestSummary] = None,
        forward_summary: Optional[BacktestSummary] = None,
        pending_count: int = 0,
    ) -> str:
        meta = metadata or {}
        if not results:
            base = f"📊 台股均線回踩選股\n日期：{scan_date}\n\n今日無符合條件個股。"
        else:
            grade_a = [r for r in results if r.grade == "A"]
            grade_b = [r for r in results if r.grade == "B"]
            lines = [
                "📊 台股均線回踩選股",
                f"日期：{scan_date}",
                f"符合：{len(results)} 檔（A 級 {len(grade_a)} / B 級 {len(grade_b)}）",
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

            base = "\n".join(lines)

        sections = [base]
        sections.append(format_backtest_section(historical_summary, "回測統計（近3年）"))
        sections.append(format_backtest_section(forward_summary, "前瞻追蹤（累計）"))
        if pending_count > 0:
            sections.append(f"追蹤中：{pending_count} 檔尚未結算")
        return "\n\n".join(sections)

    def notify_scan_results(
        self,
        results: List[GradedScreenResult],
        stock_names: Dict[str, str],
        chart_paths: Dict[str, Path],
        scan_date: str,
        metadata: Optional[Dict[str, StockMetadata]] = None,
        batch_delay: float = 1.0,
        historical_summary: Optional[BacktestSummary] = None,
        forward_summary: Optional[BacktestSummary] = None,
        pending_count: int = 0,
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
            historical_summary=historical_summary,
            forward_summary=forward_summary,
            pending_count=pending_count,
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
