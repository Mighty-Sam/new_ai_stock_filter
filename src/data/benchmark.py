"""0050 基準指數日線抓取與快取。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.data.price_fetcher import PriceFetcher
from src.data.twse_fetcher import fetch_twse_stock_history

logger = logging.getLogger(__name__)

BENCHMARK_CODE = "0050"
CACHE_PATH = Path("data/benchmark_0050.parquet")
CACHE_TTL_HOURS = 24


def _cache_fresh(path: Path, ttl_hours: int = CACHE_TTL_HOURS) -> bool:
    if not path.exists():
        return False
    age = pd.Timestamp.now() - pd.Timestamp(path.stat().st_mtime, unit="s")
    return age.total_seconds() < ttl_hours * 3600


def _load_stale_cache() -> pd.DataFrame | None:
    if not CACHE_PATH.exists():
        return None
    try:
        df = pd.read_parquet(CACHE_PATH)
        if not df.empty:
            logger.warning("使用過期 0050 快取 (%d 根)", len(df))
            return df
    except Exception as exc:
        logger.warning("讀取 0050 快取失敗: %s", exc)
    return None


def _fetch_from_apis(days: int = 1200) -> pd.DataFrame | None:
    end = date.today()
    start = end - timedelta(days=int(days * 1.6))

    fetcher = PriceFetcher(delay=0)
    df = fetcher.fetch(BENCHMARK_CODE, days=days, end_date=end, min_rows=30)
    if df is not None and not df.empty:
        logger.info("0050 來源: FinMind/yfinance (%d 根)", len(df))
        return df

    logger.info("0050 FinMind/yfinance 不可用，改用 TWSE API")
    twse_df = fetch_twse_stock_history(BENCHMARK_CODE, start, end)
    if twse_df is not None and not twse_df.empty:
        logger.info("0050 來源: TWSE (%d 根)", len(twse_df))
        return twse_df

    return None


def fetch_benchmark(days: int = 1200, force_refresh: bool = False) -> pd.DataFrame:
    """取得 0050 日線 OHLCV，優先讀快取。"""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not force_refresh and _cache_fresh(CACHE_PATH):
        try:
            df = pd.read_parquet(CACHE_PATH)
            if not df.empty:
                logger.info("使用 0050 快取 (%d 根)", len(df))
                return df
        except Exception as exc:
            logger.warning("讀取 0050 快取失敗: %s", exc)

    df = _fetch_from_apis(days=days)
    if df is None or df.empty:
        stale = _load_stale_cache()
        if stale is not None:
            return stale
        raise RuntimeError("無法取得 0050 基準資料（FinMind/yfinance/TWSE 均失敗）")

    df.to_parquet(CACHE_PATH)
    logger.info("已更新 0050 快取 (%d 根)", len(df))
    return df
