"""共用的全市場掃描骨架（交易日判斷 + 平行抓價 + 逐檔評估）。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from typing import Callable, Dict, Generic, List, Optional, Tuple, TypeVar

import pandas as pd
from tqdm import tqdm

from src.data.price_fetcher import PriceFetcher
from src.data.stock_list import get_stock_list

logger = logging.getLogger(__name__)

R = TypeVar("R")

StockProcessor = Callable[[str, PriceFetcher], Tuple[str, Optional[R], Optional[pd.DataFrame]]]


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


@dataclass
class ScanRun(Generic[R]):
    """單次全市場掃描的原始輸出，供各策略模組再加工成自己的 Output dataclass。"""

    results: List[R]
    price_data: Dict[str, pd.DataFrame]
    scan_date: date
    is_trading_day: bool
    total_codes: int


def run_market_scan(
    process_stock: StockProcessor,
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    end_date: Optional[date] = None,
    desc: str = "掃描中",
    trading_day: Optional[bool] = None,
) -> ScanRun[R]:
    """對全市場股票清單平行呼叫 process_stock(stock_code, fetcher)，收集結果與 K 線資料。

    process_stock 需回傳 (stock_code, result_or_None, df_or_None)；額外參數（end_date、
    自訂資料表等）請以 functools.partial 綁定後再傳入。trading_day 可由呼叫端傳入已知的
    交易日判斷結果，避免同一次執行內對多個掃描器重複打 API 判斷。
    """
    stocks = get_stock_list()
    codes = sorted(stocks.keys())
    if stock_limit:
        codes = codes[:stock_limit]

    fetcher = PriceFetcher()
    ref_date = end_date or date.today()
    trading = trading_day if trading_day is not None else is_trading_day(fetcher, ref_date)

    results: List[R] = []
    price_data: Dict[str, pd.DataFrame] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_stock, code, fetcher): code
            for code in codes
        }
        iterator = tqdm(as_completed(futures), total=len(futures), desc=desc)
        for future in iterator:
            code, result, df = future.result()
            if df is not None:
                price_data[code] = df
            if result is not None:
                results.append(result)

    return ScanRun(
        results=results,
        price_data=price_data,
        scan_date=ref_date,
        is_trading_day=trading,
        total_codes=len(codes),
    )
