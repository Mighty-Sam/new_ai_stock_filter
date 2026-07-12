"""全市場掃描。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from functools import partial
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.data.price_fetcher import PriceFetcher
from src.indicators.moving_average import add_moving_averages
from src.screener.conditions import ScreenResult, evaluate
from src.screener.grading import GradedScreenResult, grade_screen_result, sort_graded_results
from src.screener.scan_runner import ScanRun, run_market_scan

logger = logging.getLogger(__name__)


@dataclass
class ScanOutput:
    results: List[GradedScreenResult]
    price_data: Dict[str, pd.DataFrame]
    scan_date: date
    is_trading_day: bool

    @property
    def grade_a(self) -> List[GradedScreenResult]:
        return [r for r in self.results if r.grade == "A"]

    @property
    def grade_b(self) -> List[GradedScreenResult]:
        return [r for r in self.results if r.grade == "B"]


def _process_stock(
    stock_code: str,
    fetcher: PriceFetcher,
    end_date: Optional[date] = None,
) -> Tuple[str, Optional[GradedScreenResult], Optional[pd.DataFrame]]:
    df = fetcher.fetch(stock_code, end_date=end_date)
    if df is None:
        return stock_code, None, None

    df = add_moving_averages(df)
    v1_result = evaluate(df, stock_code=stock_code)
    if v1_result is None:
        return stock_code, None, df

    graded = grade_screen_result(df, v1_result)
    return stock_code, graded, df


def scan_market(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    end_date: Optional[date] = None,
    trading_day: Optional[bool] = None,
) -> ScanOutput:
    run: ScanRun[GradedScreenResult] = run_market_scan(
        partial(_process_stock, end_date=end_date),
        max_workers=max_workers,
        stock_limit=stock_limit,
        end_date=end_date,
        desc="掃描中",
        trading_day=trading_day,
    )

    results = sort_graded_results(run.results)
    a_count = sum(1 for r in results if r.grade == "A")
    logger.info(
        "掃描完成：%d 檔符合（A 級 %d / B 級 %d）/ %d 檔",
        len(results),
        a_count,
        len(results) - a_count,
        run.total_codes,
    )

    return ScanOutput(
        results=results,
        price_data=run.price_data,
        scan_date=run.scan_date,
        is_trading_day=run.is_trading_day,
    )
