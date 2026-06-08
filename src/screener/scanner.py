"""全市場掃描。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

import pandas as pd
from tqdm import tqdm

from src.data.price_fetcher import PriceFetcher
from src.data.stock_list import get_stock_list
from src.indicators.moving_average import add_moving_averages
from src.screener.conditions import ScreenResult, evaluate
from src.screener.grading import GradedScreenResult, grade_screen_result, sort_graded_results

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
) -> tuple[str, Optional[GradedScreenResult], Optional[pd.DataFrame]]:
    df = fetcher.fetch(stock_code, end_date=end_date)
    if df is None:
        return stock_code, None, None

    df = add_moving_averages(df)
    v1_result = evaluate(df, stock_code=stock_code)
    if v1_result is None:
        return stock_code, None, df

    graded = grade_screen_result(df, v1_result)
    return stock_code, graded, df


def is_trading_day(fetcher: PriceFetcher, reference: Optional[date] = None) -> bool:
    """判斷 reference 是否為台股交易日（週末略過；平日不因 K 棒尚未更新而略過）。"""
    ref = reference or date.today()
    if ref.weekday() >= 5:
        return False

    df = fetcher.fetch("2330", days=20, end_date=ref, min_rows=1)
    if df is None or df.empty:
        logger.warning("無法取得 2330 資料判斷交易日，假設為交易日")
        return True

    latest = df.index[-1].date()
    if latest >= ref:
        return True

    gap = (ref - latest).days
    # 週一僅有週五 K 棒（gap=3）或資料源延遲時，仍應執行掃描
    if gap <= 4:
        logger.info(
            "2330 最新 K 棒 %s（早於 %s %d 天），仍視為交易日",
            latest,
            ref,
            gap,
        )
        return True

    logger.info("2330 最新 K 棒 %s，距 %s 已 %d 天，視為非交易日", latest, ref, gap)
    return False


def scan_market(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    end_date: Optional[date] = None,
) -> ScanOutput:
    stocks = get_stock_list()
    codes = sorted(stocks.keys())
    if stock_limit:
        codes = codes[:stock_limit]

    fetcher = PriceFetcher()
    ref_date = end_date or date.today()
    trading = is_trading_day(fetcher, ref_date)

    results: List[GradedScreenResult] = []
    price_data: Dict[str, pd.DataFrame] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process_stock, code, fetcher, end_date): code
            for code in codes
        }
        iterator = tqdm(as_completed(futures), total=len(futures), desc="掃描中")
        for future in iterator:
            code, graded, df = future.result()
            if graded is not None and df is not None:
                results.append(graded)
                price_data[code] = df

    results = sort_graded_results(results)
    a_count = sum(1 for r in results if r.grade == "A")
    logger.info(
        "掃描完成：%d 檔符合（A 級 %d / B 級 %d）/ %d 檔",
        len(results),
        a_count,
        len(results) - a_count,
        len(codes),
    )

    return ScanOutput(
        results=results,
        price_data=price_data,
        scan_date=ref_date,
        is_trading_day=trading,
    )
