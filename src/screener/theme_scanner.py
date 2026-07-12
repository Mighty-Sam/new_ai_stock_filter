"""低位題材動能全市場掃描。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from functools import partial
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.data.market_cap import get_market_caps
from src.data.price_fetcher import PriceFetcher
from src.data.shareholding import get_shareholding
from src.data.stock_metadata import StockMetadata, get_stock_metadata, lookup_metadata
from src.indicators.moving_average import add_moving_averages
from src.screener.scan_runner import ScanRun, run_market_scan
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
) -> Tuple[str, Optional[ThemeScreenResult], Optional[pd.DataFrame]]:
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
    trading_day: Optional[bool] = None,
) -> ThemeScanOutput:
    metadata = get_stock_metadata()
    market_caps = get_market_caps()
    holdings = get_shareholding()

    run: ScanRun[ThemeScreenResult] = run_market_scan(
        partial(
            _process_theme_stock,
            market_caps=market_caps,
            holdings=holdings,
            metadata=metadata,
            end_date=end_date,
        ),
        max_workers=max_workers,
        stock_limit=stock_limit,
        end_date=end_date,
        desc="題材動能掃描",
        trading_day=trading_day,
    )

    filtered, hot = filter_by_hot_industries(run.results)
    logger.info(
        "題材動能掃描：第一階段 %d 檔 → 熱門產業 %s → 最終 %d 檔 / %d 檔",
        len(run.results),
        "、".join(hot) if hot else "—",
        len(filtered),
        run.total_codes,
    )

    return ThemeScanOutput(
        results=filtered,
        price_data=run.price_data,
        scan_date=run.scan_date,
        is_trading_day=run.is_trading_day,
        hot_industries=hot,
        stage1_count=len(run.results),
    )
