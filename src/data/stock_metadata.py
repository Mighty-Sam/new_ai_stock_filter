"""台股產業 / 族群 metadata（FinMind + 快取）。"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
CACHE_PATH = Path("data/stock_metadata.json")
CACHE_TTL_DAYS = 7
MISSING = "—"


@dataclass(frozen=True)
class StockMetadata:
    industry: str
    groups: tuple[str, ...]

    @property
    def groups_display(self) -> str:
        if not self.groups:
            return MISSING
        return "、".join(self.groups)

    def groups_display_truncated(self, max_len: int = 40) -> str:
        text = self.groups_display
        if text == MISSING or len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"


def _cache_fresh(path: Path, ttl_days: int = CACHE_TTL_DAYS) -> bool:
    if not path.exists():
        return False
    age = pd.Timestamp.now() - pd.Timestamp(path.stat().st_mtime, unit="s")
    return age.days < ttl_days


def _finmind_headers() -> dict:
    token = os.getenv("FINMIND_TOKEN", "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def fetch_industry_categories() -> Dict[str, str]:
    """TaiwanStockInfo → stock_id → industry_category。"""
    params = {"dataset": "TaiwanStockInfo"}
    try:
        response = requests.get(
            FINMIND_API_URL,
            params=params,
            headers=_finmind_headers(),
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != 200:
            logger.warning("FinMind TaiwanStockInfo 回應異常: %s", payload.get("msg"))
            return {}

        result: Dict[str, str] = {}
        for item in payload.get("data") or []:
            code = str(item.get("stock_id", "")).strip()
            if not code.isdigit() or len(code) != 4:
                continue
            industry = str(item.get("industry_category", "") or "").strip()
            if industry:
                result[code] = industry
        logger.info("FinMind 產業別 %d 檔", len(result))
        return result
    except Exception as exc:
        logger.warning("FinMind TaiwanStockInfo 失敗: %s", exc)
        return {}


def fetch_industry_groups() -> Dict[str, tuple[str, ...]]:
    """TaiwanStockIndustryChain → stock_id → 去重 sub_industry。"""
    params = {"dataset": "TaiwanStockIndustryChain"}
    try:
        response = requests.get(
            FINMIND_API_URL,
            params=params,
            headers=_finmind_headers(),
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != 200:
            logger.warning("FinMind TaiwanStockIndustryChain 回應異常: %s", payload.get("msg"))
            return {}

        groups_map: dict[str, set[str]] = defaultdict(set)
        for item in payload.get("data") or []:
            code = str(item.get("stock_id", "")).strip()
            if not code.isdigit() or len(code) != 4:
                continue
            sub = str(item.get("sub_industry", "") or "").strip()
            if sub:
                groups_map[code].add(sub)

        result = {code: tuple(sorted(subs)) for code, subs in groups_map.items()}
        logger.info("FinMind 族群 sub_industry %d 檔", len(result))
        return result
    except Exception as exc:
        logger.warning("FinMind TaiwanStockIndustryChain 失敗: %s", exc)
        return {}


def merge_metadata(
    industries: Dict[str, str],
    groups: Dict[str, tuple[str, ...]],
) -> Dict[str, StockMetadata]:
    codes = set(industries.keys()) | set(groups.keys())
    merged: Dict[str, StockMetadata] = {}
    for code in codes:
        merged[code] = StockMetadata(
            industry=industries.get(code, MISSING),
            groups=groups.get(code, ()),
        )
    return merged


def _metadata_to_dict(data: Dict[str, StockMetadata]) -> dict:
    return {
        code: {"industry": m.industry, "groups": list(m.groups)}
        for code, m in data.items()
    }


def _metadata_from_dict(raw: dict) -> Dict[str, StockMetadata]:
    result: Dict[str, StockMetadata] = {}
    for code, item in raw.items():
        if not isinstance(item, dict):
            continue
        groups = item.get("groups") or []
        result[str(code)] = StockMetadata(
            industry=str(item.get("industry") or MISSING),
            groups=tuple(groups),
        )
    return result


def load_metadata_cache(path: Optional[Path] = None) -> Optional[Dict[str, StockMetadata]]:
    path = path or CACHE_PATH
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = raw.get("stocks") if isinstance(raw, dict) and "stocks" in raw else raw
        if isinstance(data, dict) and len(data) > 100:
            return _metadata_from_dict(data)
    except Exception as exc:
        logger.warning("讀取 metadata 快取失敗: %s", exc)
    return None


def save_metadata_cache(
    data: Dict[str, StockMetadata],
    path: Optional[Path] = None,
) -> None:
    path = path or CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": pd.Timestamp.now().isoformat(),
        "stocks": _metadata_to_dict(data),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_all_metadata() -> Dict[str, StockMetadata]:
    industries = fetch_industry_categories()
    groups = fetch_industry_groups()
    if not industries and not groups:
        return {}
    return merge_metadata(industries, groups)


def get_stock_metadata(use_cache: bool = True) -> Dict[str, StockMetadata]:
    path = CACHE_PATH
    if use_cache and _cache_fresh(path):
        cached = load_metadata_cache(path)
        if cached:
            logger.info("使用 metadata 快取 %d 檔", len(cached))
            return cached

    data = fetch_all_metadata()
    if data:
        save_metadata_cache(data)
        return data

    stale = load_metadata_cache(path)
    if stale:
        logger.warning("FinMind metadata 抓取失敗，使用過期快取 %d 檔", len(stale))
        return stale

    logger.warning("無 metadata 可用，推播將顯示 —")
    return {}


def lookup_metadata(
    metadata: Dict[str, StockMetadata],
    stock_code: str,
) -> StockMetadata:
    return metadata.get(stock_code, StockMetadata(industry=MISSING, groups=()))
