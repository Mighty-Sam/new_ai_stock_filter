"""台灣證交所 STOCK_DAY 月線資料（0050 等 ETF/個股備援）。"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.twse.com.tw/",
}


def _parse_roc_date(s: str) -> pd.Timestamp:
    """114/05/02 → 2025-05-02"""
    parts = str(s).strip().split("/")
    if len(parts) != 3:
        raise ValueError(f"無法解析日期: {s}")
    year = int(parts[0]) + 1911
    month = int(parts[1])
    day = int(parts[2])
    return pd.Timestamp(date(year, month, day))


def _parse_number(s: str) -> float:
    return float(str(s).replace(",", "").replace("--", "0") or 0)


def fetch_twse_stock_month(stock_code: str, year: int, month: int) -> pd.DataFrame:
    """抓取單月日線（TWSE STOCK_DAY API）。"""
    query_date = f"{year}{month:02d}01"
    url = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
    params = {"response": "json", "date": query_date, "stockNo": stock_code}

    response = requests.get(url, params=params, headers=_HEADERS, timeout=20)
    response.raise_for_status()
    payload = response.json()

    if payload.get("stat") != "OK":
        return pd.DataFrame()

    rows = payload.get("data") or []
    if not rows:
        return pd.DataFrame()

    records = []
    for row in rows:
        if len(row) < 7:
            continue
        try:
            ts = _parse_roc_date(row[0])
            records.append(
                {
                    "date": ts,
                    "volume": _parse_number(row[1]),
                    "open": _parse_number(row[3]),
                    "high": _parse_number(row[4]),
                    "low": _parse_number(row[5]),
                    "close": _parse_number(row[6]),
                }
            )
        except (ValueError, IndexError):
            continue

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records).set_index("date").sort_index()
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def fetch_twse_stock_history(
    stock_code: str,
    start_date: date,
    end_date: date,
    delay: float = 0.3,
) -> Optional[pd.DataFrame]:
    """抓取 TWSE 月線 API 組合成的日線歷史。"""
    frames: list[pd.DataFrame] = []
    y, m = start_date.year, start_date.month
    end_y, end_m = end_date.year, end_date.month

    while (y, m) <= (end_y, end_m):
        try:
            month_df = fetch_twse_stock_month(stock_code, y, m)
            if not month_df.empty:
                frames.append(month_df)
        except Exception as exc:
            logger.debug("TWSE %s %04d-%02d 失敗: %s", stock_code, y, m, exc)
        time.sleep(delay)
        m += 1
        if m > 12:
            m = 1
            y += 1

    if not frames:
        return None

    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[(df.index.date >= start_date) & (df.index.date <= end_date)]
    return df if not df.empty else None
