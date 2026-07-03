"""三大法人買賣超（FinMind Wide 表）。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd
import requests

from src.data.stock_metadata import FINMIND_API_URL, _finmind_headers

logger = logging.getLogger(__name__)

FETCH_CALENDAR_DAYS = 30
LOOKBACK_TRADING_DAYS = 10
SUMMARY_DAYS = 5
SHARES_PER_LOT = 1000


@dataclass(frozen=True)
class InstitutionalFlow:
    stock_code: str
    as_of_date: date
    foreign_net_lots: float
    trust_net_lots: float
    dealer_net_lots: float
    total_net_lots: float
    sum_5d_net_lots: float
    consecutive_buy_days: int

    @property
    def is_net_buy(self) -> bool:
        return self.total_net_lots > 0


def _shares_to_lots(shares: float) -> float:
    return round(shares / SHARES_PER_LOT, 1)


def _row_net_lots(row: pd.Series) -> tuple[float, float, float, float]:
    foreign = (
        float(row.get("Foreign_Investor_buy", 0) or 0)
        - float(row.get("Foreign_Investor_sell", 0) or 0)
        + float(row.get("Foreign_Dealer_Self_buy", 0) or 0)
        - float(row.get("Foreign_Dealer_Self_sell", 0) or 0)
    )
    trust = float(row.get("Investment_Trust_buy", 0) or 0) - float(
        row.get("Investment_Trust_sell", 0) or 0
    )
    dealer = (
        float(row.get("Dealer_buy", 0) or 0)
        - float(row.get("Dealer_sell", 0) or 0)
        + float(row.get("Dealer_self_buy", 0) or 0)
        - float(row.get("Dealer_self_sell", 0) or 0)
        + float(row.get("Dealer_Hedging_buy", 0) or 0)
        - float(row.get("Dealer_Hedging_sell", 0) or 0)
    )
    total = foreign + trust + dealer
    return (
        _shares_to_lots(foreign),
        _shares_to_lots(trust),
        _shares_to_lots(dealer),
        _shares_to_lots(total),
    )


def _consecutive_buy_days(daily_totals: List[float]) -> int:
    """由最近一日往回數連續買超天數。"""
    count = 0
    for value in reversed(daily_totals):
        if value > 0:
            count += 1
        else:
            break
    return count


def compute_institutional_flow(df: pd.DataFrame, stock_code: str) -> Optional[InstitutionalFlow]:
    """由 Wide 表 DataFrame 計算籌碼摘要。"""
    if df is None or df.empty:
        return None

    work = df.copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work.sort_values("date").tail(LOOKBACK_TRADING_DAYS)
    if work.empty:
        return None

    daily: List[tuple[date, float, float, float, float]] = []
    for _, row in work.iterrows():
        foreign, trust, dealer, total = _row_net_lots(row)
        daily.append((row["date"].date(), foreign, trust, dealer, total))

    latest = daily[-1]
    as_of = latest[0]
    sum_5d = sum(item[4] for item in daily[-SUMMARY_DAYS:])
    streak = _consecutive_buy_days([item[4] for item in daily])

    return InstitutionalFlow(
        stock_code=stock_code,
        as_of_date=as_of,
        foreign_net_lots=latest[1],
        trust_net_lots=latest[2],
        dealer_net_lots=latest[3],
        total_net_lots=latest[4],
        sum_5d_net_lots=round(sum_5d, 1),
        consecutive_buy_days=streak,
    )


def fetch_institutional_wide(
    stock_code: str,
    end_date: Optional[date] = None,
) -> Optional[pd.DataFrame]:
    """抓取單檔法人買賣 Wide 表。"""
    token = os.getenv("FINMIND_TOKEN", "").strip()
    if not token:
        logger.debug("FINMIND_TOKEN 未設定，略過法人籌碼")
        return None

    end = end_date or date.today()
    start = end - timedelta(days=FETCH_CALENDAR_DAYS)
    params = {
        "dataset": "TaiwanStockInstitutionalInvestorsBuySellWide",
        "data_id": stock_code,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }

    try:
        response = requests.get(
            FINMIND_API_URL,
            params=params,
            headers=_finmind_headers(),
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != 200:
            logger.warning("法人籌碼 %s API 異常: %s", stock_code, payload.get("msg"))
            return None
        rows = payload.get("data") or []
        if not rows:
            return None
        return pd.DataFrame(rows)
    except Exception as exc:
        logger.warning("法人籌碼 %s 抓取失敗: %s", stock_code, exc)
        return None


def get_institutional_flow(
    stock_code: str,
    end_date: Optional[date] = None,
) -> Optional[InstitutionalFlow]:
    df = fetch_institutional_wide(stock_code, end_date=end_date)
    return compute_institutional_flow(df, stock_code)


def fetch_institutional_flows(
    stock_codes: List[str],
    end_date: Optional[date] = None,
) -> Dict[str, InstitutionalFlow]:
    """批次查詢多檔法人籌碼。"""
    result: Dict[str, InstitutionalFlow] = {}
    for code in stock_codes:
        flow = get_institutional_flow(code, end_date=end_date)
        if flow is not None:
            result[code] = flow
    return result


def _format_lot_signed(lots: float) -> str:
    arrow = "↑" if lots > 0 else "↓" if lots < 0 else "→"
    sign = "+" if lots > 0 else ""
    return f"{sign}{lots:.0f}張{arrow}"


def format_chip_line(flow: InstitutionalFlow, indent: bool = True) -> str:
    """格式化三大法人籌碼一行文字。"""
    date_str = flow.as_of_date.strftime("%m/%d")
    parts = [
        f"外資{_format_lot_signed(flow.foreign_net_lots)}",
        f"投信{_format_lot_signed(flow.trust_net_lots)}",
        f"自營{_format_lot_signed(flow.dealer_net_lots)}",
    ]
    sum5 = flow.sum_5d_net_lots
    sum5_arrow = "↑" if sum5 > 0 else "↓" if sum5 < 0 else "→"
    sum5_sign = "+" if sum5 > 0 else ""
    streak = f" 連{flow.consecutive_buy_days}日買超" if flow.consecutive_buy_days > 0 else ""
    prefix = "   " if indent else ""
    return (
        f"{prefix}籌碼({date_str})：{' '.join(parts)}"
        f" | 近5日{sum5_sign}{sum5:.0f}張{sum5_arrow}{streak}"
    )
