"""台股董監持股比例（TWSE 公開資料 + 快取）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from src.data.twse_opendata import fetch_director_holding_pct

logger = logging.getLogger(__name__)

CACHE_PATH = Path("data/shareholding.json")
CACHE_TTL_DAYS = 7


def _cache_fresh(path: Path, ttl_days: int = CACHE_TTL_DAYS) -> bool:
    if not path.exists():
        return False
    age = pd.Timestamp.now() - pd.Timestamp(path.stat().st_mtime, unit="s")
    return age.days < ttl_days


def load_shareholding_cache(path: Optional[Path] = None) -> Optional[Dict[str, float]]:
    path = path or CACHE_PATH
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        holdings = raw.get("holdings") if isinstance(raw, dict) else raw
        if isinstance(holdings, dict) and len(holdings) > 50:
            return {str(k): float(v) for k, v in holdings.items()}
    except Exception as exc:
        logger.warning("讀取持股快取失敗: %s", exc)
    return None


def save_shareholding_cache(data: Dict[str, float], path: Optional[Path] = None) -> None:
    path = path or CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": pd.Timestamp.now().isoformat(),
        "source": "twse_opendata",
        "holdings": {k: round(v, 2) for k, v in data.items()},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_shareholding(use_cache: bool = True) -> Dict[str, float]:
    path = CACHE_PATH
    if use_cache and _cache_fresh(path):
        cached = load_shareholding_cache(path)
        if cached:
            logger.info("使用董監持股快取 %d 檔", len(cached))
            return cached

    data = fetch_director_holding_pct()
    if data:
        save_shareholding_cache(data)
        return data

    stale = load_shareholding_cache(path)
    if stale:
        logger.warning("董監持股抓取失敗，使用過期快取 %d 檔", len(stale))
        return stale

    logger.warning("無董監持股資料可用")
    return {}
