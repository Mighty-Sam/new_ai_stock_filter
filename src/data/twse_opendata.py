"""台灣證交所 / 櫃買中心公開資料（市值、董監持股備援）。"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

TWSE_OPENAPI = "https://openapi.twse.com.tw/v1/opendata"
TPEX_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.twse.com.tw/",
}


def _parse_int(raw: object) -> int:
    if raw is None:
        return 0
    text = str(raw).strip().replace(",", "")
    if not text or text in {"-", "--", "----"}:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _parse_float(raw: object) -> Optional[float]:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text in {"-", "--", "----"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_par_value(raw: object) -> float:
    text = str(raw or "")
    match = re.search(r"([\d.]+)", text)
    if not match:
        return 10.0
    try:
        val = float(match.group(1))
        return val if val > 0 else 10.0
    except ValueError:
        return 10.0


def _fetch_opendata(endpoint: str, timeout: int = 90) -> list:
    url = f"{TWSE_OPENAPI}/{endpoint}"
    response = requests.get(url, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"{endpoint} 回應格式異常")
    return payload


def _shares_from_profile_row(row: dict) -> int:
    shares = _parse_int(row.get("已發行普通股數或TDR原股發行股數"))
    if shares > 0:
        return shares
    capital = _parse_int(row.get("實收資本額"))
    if capital <= 0:
        return 0
    par = _parse_par_value(row.get("普通股每股面額"))
    return int(capital / par)


def fetch_listed_shares_outstanding() -> Dict[str, int]:
    """上市發行股數（t187ap03_L）。"""
    rows = _fetch_opendata("t187ap03_L")
    result: Dict[str, int] = {}
    for row in rows:
        code = str(row.get("公司代號", "")).strip()
        if not code.isdigit() or len(code) != 4:
            continue
        shares = _shares_from_profile_row(row)
        if shares > 0:
            result[code] = shares
    logger.info("TWSE 上市發行股數 %d 檔", len(result))
    return result


def fetch_otc_shares_outstanding() -> Dict[str, int]:
    """上櫃發行股數（t187ap03_P + TPEX quotes Capitals 備援）。"""
    result: Dict[str, int] = {}
    try:
        rows = _fetch_opendata("t187ap03_P")
        for row in rows:
            code = str(row.get("公司代號", "")).strip()
            if not code.isdigit() or len(code) != 4:
                continue
            shares = _shares_from_profile_row(row)
            if shares > 0:
                result[code] = shares
    except Exception as exc:
        logger.warning("TWSE 上櫃基本資料失敗: %s", exc)

    try:
        response = requests.get(
            TPEX_QUOTES_URL,
            headers={**_HEADERS, "Referer": "https://www.tpex.org.tw/"},
            timeout=30,
        )
        response.raise_for_status()
        for row in response.json():
            code = str(row.get("SecuritiesCompanyCode", "")).strip()
            if not code.isdigit() or len(code) != 4 or code in result:
                continue
            shares = _parse_int(row.get("Capitals"))
            if shares > 0:
                result[code] = shares
    except Exception as exc:
        logger.warning("TPEX 發行股數備援失敗: %s", exc)

    logger.info("上櫃發行股數 %d 檔", len(result))
    return result


def fetch_listed_closes() -> Dict[str, float]:
    """上市最新收盤價（BWIBBU_d）。"""
    for days_back in range(12):
        date_str = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
        url = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
        params = {"response": "json", "date": date_str, "selectType": "ALL"}
        try:
            response = requests.get(url, params=params, headers=_HEADERS, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if payload.get("stat") != "OK":
                continue

            closes: Dict[str, float] = {}
            for row in payload.get("data") or []:
                if not row or len(row) < 3:
                    continue
                code = str(row[0]).strip()
                close = _parse_float(row[2])
                if code.isdigit() and len(code) == 4 and close is not None and close > 0:
                    closes[code] = close

            if len(closes) > 100:
                logger.info("TWSE 收盤價 %d 檔（%s）", len(closes), date_str)
                return closes
        except Exception as exc:
            logger.debug("BWIBBU %s 失敗: %s", date_str, exc)

    logger.warning("無法取得 TWSE 收盤價")
    return {}


def fetch_otc_closes() -> Dict[str, float]:
    """上櫃最新收盤價（TPEX openapi quotes）。"""
    try:
        response = requests.get(
            TPEX_QUOTES_URL,
            headers={**_HEADERS, "Referer": "https://www.tpex.org.tw/"},
            timeout=30,
        )
        response.raise_for_status()
        closes: Dict[str, float] = {}
        for row in response.json():
            code = str(row.get("SecuritiesCompanyCode", "")).strip()
            close = _parse_float(row.get("Close"))
            if code.isdigit() and len(code) == 4 and close is not None and close > 0:
                closes[code] = close
        logger.info("TPEX 收盤價 %d 檔", len(closes))
        return closes
    except Exception as exc:
        logger.warning("TPEX 收盤價失敗: %s", exc)
        return {}


def fetch_market_caps_from_twse() -> Dict[str, float]:
    """收盤價 × 發行股數 → 市值（億台幣）。"""
    listed_shares = fetch_listed_shares_outstanding()
    otc_shares = fetch_otc_shares_outstanding()
    listed_closes = fetch_listed_closes()
    otc_closes = fetch_otc_closes()

    result: Dict[str, float] = {}
    for code, shares in listed_shares.items():
        close = listed_closes.get(code)
        if close is None:
            continue
        result[code] = close * shares / 1e8

    for code, shares in otc_shares.items():
        close = otc_closes.get(code)
        if close is None:
            continue
        result[code] = close * shares / 1e8

    logger.info("TWSE/TPEX 市值 %d 檔", len(result))
    return result


def _aggregate_director_shares(rows: list) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    for row in rows:
        code = str(row.get("公司代號", "")).strip()
        if not code.isdigit() or len(code) != 4:
            continue
        own = _parse_int(row.get("目前持股"))
        related = _parse_int(row.get("內部人關係人目前持股合計"))
        totals[code] = totals.get(code, 0) + own + related
    return totals


def fetch_director_holding_pct() -> Dict[str, float]:
    """董監及關係人持股占發行股數比例（%）。"""
    listed_shares = fetch_listed_shares_outstanding()
    otc_shares = fetch_otc_shares_outstanding()
    shares_map = {**listed_shares, **otc_shares}

    director_shares: Dict[str, int] = {}
    for endpoint in ("t187ap11_L", "t187ap11_P"):
        try:
            rows = _fetch_opendata(endpoint)
            for code, shares in _aggregate_director_shares(rows).items():
                director_shares[code] = director_shares.get(code, 0) + shares
        except Exception as exc:
            logger.warning("董監持股 %s 失敗: %s", endpoint, exc)

    result: Dict[str, float] = {}
    for code, held in director_shares.items():
        total = shares_map.get(code)
        if not total or held <= 0:
            continue
        pct = held / total * 100
        result[code] = min(pct, 100.0)

    logger.info("TWSE 董監持股比例 %d 檔", len(result))
    return result
