"""台股日線 OHLCV 抓取（FinMind 主、yfinance 備援）。"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

from src.data.twse_fetcher import fetch_twse_stock_history

logger = logging.getLogger(__name__)

# yfinance 在查無資料時會以 ERROR 印出 delisted/404，掃描全市場時會洗版
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

_DEBUG_LOG = Path(__file__).resolve().parents[2] / ".cursor" / "debug-f16434.log"


def _agent_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "f16434",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
PRICE_CACHE_DIR = Path("data/cache/prices")
PRICE_CACHE_TTL_HOURS = 24


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """統一欄位名稱與索引。"""
    col_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
    }
    df = df.rename(columns=col_map)
    for col in OHLCV_COLUMNS:
        if col not in df.columns:
            raise ValueError(f"缺少欄位 {col}")

    if not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        else:
            df.index = pd.to_datetime(df.index)

    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    # 統一為 naive datetime（台灣日期），避免 tz 比較錯誤
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Taipei").tz_localize(None)
    df.index = df.index.normalize()
    return df[OHLCV_COLUMNS].astype(float)


def _fetch_finmind(
    stock_code: str,
    start_date: date,
    end_date: date,
    token: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    token = token or os.getenv("FINMIND_TOKEN")
    if not token:
        return None

    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_code,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "token": token,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        if status == 402:
            _agent_log(
                "H3",
                "price_fetcher._fetch_finmind",
                "finmind_rate_limited",
                {"stock_code": stock_code},
            )
        if status != 200:
            return None

        rows = payload.get("data") or []
        if not rows:
            _agent_log(
                "H2",
                "price_fetcher._fetch_finmind",
                "finmind_empty",
                {"stock_code": stock_code},
            )
            return None

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        return _normalize_df(df)
    except Exception as exc:
        logger.debug("FinMind %s 失敗: %s", stock_code, exc)
        return None


def _ticker_symbol(stock_code: str, market: str = "TWSE") -> str:
    suffix = ".TW" if market == "TWSE" else ".TWO"
    return f"{stock_code}{suffix}"


def _fetch_yfinance(
    stock_code: str,
    start_date: date,
    end_date: date,
) -> Optional[pd.DataFrame]:
    _agent_log(
        "H1",
        "price_fetcher._fetch_yfinance",
        "yfinance_fallback",
        {"stock_code": stock_code},
    )
    for suffix in (".TW", ".TWO"):
        try:
            ticker = yf.Ticker(f"{stock_code}{suffix}")
            df = ticker.history(
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                auto_adjust=True,
            )
            if df is None or df.empty:
                continue
            return _normalize_df(df)
        except Exception as exc:
            logger.debug("yfinance %s%s 失敗: %s", stock_code, suffix, exc)
    return None


def _cache_fresh(path: Path, ttl_hours: int = PRICE_CACHE_TTL_HOURS) -> bool:
    if not path.exists():
        return False
    age = pd.Timestamp.now() - pd.Timestamp(path.stat().st_mtime, unit="s")
    return age.total_seconds() < ttl_hours * 3600


def _load_price_cache(stock_code: str) -> Optional[pd.DataFrame]:
    path = PRICE_CACHE_DIR / f"{stock_code}.parquet"
    if not _cache_fresh(path, ttl_hours=72):
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def _save_price_cache(stock_code: str, df: pd.DataFrame) -> None:
    PRICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = PRICE_CACHE_DIR / f"{stock_code}.parquet"
    df.to_parquet(path)


class PriceFetcher:
    """台股日線資料抓取器。"""

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.token = os.getenv("FINMIND_TOKEN")

    def fetch(
        self,
        stock_code: str,
        days: int = 200,
        end_date: Optional[date] = None,
        min_rows: int = 130,
        use_cache: bool = True,
        use_twse_fallback: bool = True,
    ) -> Optional[pd.DataFrame]:
        end = end_date or date.today()
        start = end - timedelta(days=int(days * 1.6))

        if use_cache:
            cached = _load_price_cache(stock_code)
            if cached is not None and not cached.empty:
                cutoff = pd.Timestamp(end)
                subset = cached[cached.index <= cutoff]
                last_bar = subset.index[-1].date() if not subset.empty else None
                stale = last_bar is None or (end - last_bar).days > 14
                # 請求截止為今日時，快取若落後於今日則重抓（週一常見僅有週五 K 棒）
                if (
                    not stale
                    and last_bar is not None
                    and end == date.today()
                    and last_bar < end
                    and (end - last_bar).days <= 7
                ):
                    stale = True
                if not stale and len(subset) >= min_rows:
                    return subset

        df = _fetch_finmind(stock_code, start, end, self.token)
        if df is None or df.empty:
            df = _fetch_yfinance(stock_code, start, end)
        if (df is None or df.empty) and use_twse_fallback:
            logger.debug("%s FinMind/yfinance 失敗，改用 TWSE", stock_code)
            df = fetch_twse_stock_history(stock_code, start, end, delay=0.2)

        if self.delay > 0:
            time.sleep(self.delay)

        if df is None or df.empty:
            _agent_log(
                "H2",
                "price_fetcher.fetch",
                "all_sources_failed",
                {"stock_code": stock_code},
            )
            return None

        cutoff = pd.Timestamp(end)
        df = df[df.index <= cutoff]
        if len(df) >= min_rows:
            if use_cache:
                _save_price_cache(stock_code, df)
            return df
        _agent_log(
            "H4",
            "price_fetcher.fetch",
            "insufficient_rows",
            {"stock_code": stock_code, "rows": len(df), "min_rows": min_rows},
        )
        return None

    def fetch_as_of(
        self,
        stock_code: str,
        as_of: date,
        days: int = 200,
        use_twse_fallback: bool = True,
    ) -> Optional[pd.DataFrame]:
        """抓取截至指定日期的歷史資料（供回測 / 單元測試）。"""
        return self.fetch(
            stock_code,
            days=days,
            end_date=as_of,
            min_rows=1,
            use_cache=True,
            use_twse_fallback=use_twse_fallback,
        )
