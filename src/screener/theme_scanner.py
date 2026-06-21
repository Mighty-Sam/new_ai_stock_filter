"""低位題材動能全市場掃描。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

import pandas as pd
from tqdm import tqdm

from src.data.market_cap import get_market_caps
from src.data.price_fetcher import PriceFetcher
from src.data.shareholding import get_shareholding
from src.data.stock_list import get_stock_list
from src.data.stock_metadata import StockMetadata, get_stock_metadata, lookup_metadata
from src.indicators.moving_average import add_moving_averages
from src.screener.scanner import is_trading_day
from src.screener.theme_conditions import (
    ThemeScreenResult,
    evaluate_theme_candidate,
    filter_by_hot_industries,
)

logger = logging.getLogger(__name__)


@dataclass
class ThemeScanOutput:
    results: List[ThemeScreenResult]
    price_data: Dict[str, pd.DataFrame]
    scan_date: date
    is_trading_day: bool
    hot_industries: List[str]
    stage1_count: int


def _process_theme_stock(
    stock_code: str,
    fetcher: PriceFetcher,
    market_caps: Dict[str, float],
    holdings: Dict[str, float],
    metadata: Dict[str, StockMetadata],
    end_date: Optional[date] = None,
) -> tuple[str, Optional[ThemeScreenResult], Optional[pd.DataFrame]]:
    cap = market_caps.get(stock_code)
    holding = holdings.get(stock_code)
    if cap is None or holding is None:
        return stock_code, None, None

    df = fetcher.fetch(stock_code, end_date=end_date)
    if df is None:
        return stock_code, None, None

    df = add_moving_averages(df)
    meta = lookup_metadata(metadata, stock_code)
    result = evaluate_theme_candidate(
        df,
        stock_code,
        market_cap_billions=cap,
        director_holding_pct=holding,
        metadata=meta,
    )
    if result is None:
        return stock_code, None, df
    return stock_code, result, df


def scan_theme_momentum(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    end_date: Optional[date] = None,
) -> ThemeScanOutput:
    stocks = get_stock_list()
    codes = sorted(stocks.keys())
    if stock_limit:
        codes = codes[:stock_limit]

    metadata = get_stock_metadata()
    market_caps = get_market_caps()
    holdings = get_shareholding()

    fetcher = PriceFetcher()
    ref_date = end_date or date.today()
    trading = is_trading_day(fetcher, ref_date)

    stage1: List[ThemeScreenResult] = []
    price_data: Dict[str, pd.DataFrame] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _process_theme_stock,
                code,
                fetcher,
                market_caps,
                holdings,
                metadata,
                end_date,
            ): code
            for code in codes
        }
        iterator = tqdm(as_completed(futures), total=len(futures), desc="題材動能掃描")
        for future in iterator:
            code, result, df = future.result()
            if df is not None:
                price_data[code] = df
            if result is not None:
                stage1.append(result)

    filtered, hot = filter_by_hot_industries(stage1)
    logger.info(
        "題材動能掃描：第一階段 %d 檔 → 熱門產業 %s → 最終 %d 檔 / %d 檔",
        len(stage1),
        "、".join(hot) if hot else "—",
        len(filtered),
        len(codes),
    )

    return ThemeScanOutput(
        results=filtered,
        price_data=price_data,
        scan_date=ref_date,
        is_trading_day=trading,
        hot_industries=hot,
        stage1_count=len(stage1),
    )
