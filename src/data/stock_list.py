"""台股上市 + 上櫃個股清單（排除 ETF / 權證）。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

# 常見 ETF / 權證前綴（4 碼個股仍可能誤判，以 API 名稱二次過濾）
_EXCLUDED_PREFIXES = ("00", "02", "03", "04", "05", "06", "07", "08", "09")
_EXCLUDED_KEYWORDS = ("ETF", "指數", "權證", "認購", "認售", "牛證", "熊證", "DR")


def _is_valid_stock_code(code: str) -> bool:
    if not code.isdigit() or len(code) != 4:
        return False
    if code.startswith(_EXCLUDED_PREFIXES):
        return False
    return True


def _filter_by_name(stocks: Dict[str, str]) -> Dict[str, str]:
    filtered: Dict[str, str] = {}
    for code, name in stocks.items():
        if not _is_valid_stock_code(code):
            continue
        if any(kw in name for kw in _EXCLUDED_KEYWORDS):
            continue
        filtered[code] = name
    return filtered


def fetch_listed_stocks_from_twse() -> Dict[str, str]:
    """從 TWSE 取得上市股票代碼與名稱。"""
    headers = {**_HEADERS, "Referer": "https://www.twse.com.tw/"}

    for days_back in range(10):
        check_date = datetime.now() - timedelta(days=days_back)
        date_str = check_date.strftime("%Y%m%d")
        url = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
        params = {"response": "json", "date": date_str, "type": "MS"}

        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.encoding = "utf-8"
            if response.status_code != 200:
                continue

            data = response.json()
            if data.get("stat") != "OK":
                continue

            stocks: Dict[str, str] = {}
            for data_key in ("data9", "data5", "data"):
                rows = data.get(data_key)
                if not isinstance(rows, list):
                    continue
                for item in rows:
                    if not item or len(item) < 2:
                        continue
                    code = str(item[0]).strip()
                    name = str(item[1]).strip()
                    if _is_valid_stock_code(code):
                        stocks[code] = name

            if len(stocks) > 100:
                logger.info("TWSE 上市股票 %d 檔（日期 %s）", len(stocks), date_str)
                return stocks
        except Exception as exc:
            logger.debug("TWSE 清單抓取失敗 %s: %s", date_str, exc)

    logger.warning("無法從 TWSE 取得上市清單")
    return {}


def fetch_otc_stocks_from_tpex() -> Dict[str, str]:
    """從 TPEX 取得上櫃股票代碼與名稱。"""
    headers = {
        **_HEADERS,
        "Referer": "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote.php",
    }

    for days_back in range(10):
        check_date = datetime.now() - timedelta(days=days_back)
        date_str = check_date.strftime("%Y/%m/%d")
        url = "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php"
        params = {"l": "zh-tw", "d": date_str, "s": "0,asc"}

        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.encoding = "utf-8"
            if response.status_code != 200:
                continue

            data = response.json()
            rows = data.get("aaData")
            if not isinstance(rows, list):
                continue

            stocks: Dict[str, str] = {}
            for item in rows:
                if not item or len(item) < 2:
                    continue
                code = str(item[0]).strip()
                name = str(item[1]).strip()
                if _is_valid_stock_code(code):
                    stocks[code] = name

            if len(stocks) > 50:
                logger.info("TPEX 上櫃股票 %d 檔（日期 %s）", len(stocks), date_str)
                return stocks
        except Exception as exc:
            logger.debug("TPEX 清單抓取失敗 %s: %s", date_str, exc)

    logger.warning("無法從 TPEX 取得上櫃清單")
    return {}


def fetch_stocks_from_finmind() -> Dict[str, str]:
    """從 FinMind TaiwanStockInfo 取得股號清單（免 token）。"""
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": "TaiwanStockInfo"}
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != 200:
            return {}

        stocks: Dict[str, str] = {}
        for item in payload.get("data") or []:
            code = str(item.get("stock_id", "")).strip()
            name = str(item.get("stock_name", "")).strip()
            stock_type = str(item.get("type", "")).strip()
            if stock_type not in ("twse", "tpex"):
                continue
            if _is_valid_stock_code(code):
                stocks[code] = name

        if stocks:
            logger.info("FinMind 取得 %d 檔個股", len(stocks))
        return stocks
    except Exception as exc:
        logger.warning("FinMind 股號清單失敗: %s", exc)
        return {}


def get_all_stocks(include_otc: bool = True) -> Dict[str, str]:
    """合併上市 + 上櫃個股清單。"""
    listed = fetch_listed_stocks_from_twse()
    otc: Dict[str, str] = {}
    if include_otc:
        otc = fetch_otc_stocks_from_tpex()

    merged = {**listed, **otc}
    merged = _filter_by_name(merged)

    if len(merged) < 100:
        logger.warning("官方 API 清單不足，改用 FinMind 備援")
        finmind = fetch_stocks_from_finmind()
        if finmind:
            merged = _filter_by_name(finmind)

    return merged


def load_stock_list_cache(cache_path: Optional[Path] = None) -> Optional[Dict[str, str]]:
    path = cache_path or Path("data/stock_list.json")
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and len(data) > 100:
            return data
    except Exception as exc:
        logger.warning("讀取快取失敗: %s", exc)
    return None


def save_stock_list_cache(stocks: Dict[str, str], cache_path: Optional[Path] = None) -> None:
    path = cache_path or Path("data/stock_list.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)


def get_stock_list(use_cache: bool = True) -> Dict[str, str]:
    if use_cache:
        cached = load_stock_list_cache()
        if cached:
            logger.info("使用快取股號清單 %d 檔", len(cached))
            return cached

    stocks = get_all_stocks()
    if stocks:
        save_stock_list_cache(stocks)
    return stocks
