"""漲停量縮整理選股 — 漲停後量能逐日萎縮、股價守在漲停日區間內的洗盤蓄勢型態。

型態橫跨 4 根連續 K 棒：最新一根為 day4（訊號日），漲停日為往回第 3 根（day1）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.data.price_fetcher import PriceFetcher
from src.indicators.moving_average import add_moving_averages
from src.screener.scan_runner import ScanRun, run_market_scan
from src.screener.volume_surge import daily_gain_pct

logger = logging.getLogger(__name__)

LIMIT_UP_MIN_GAIN_PCT = 9.5  # 漲停近似門檻（昨收×1.1 依分檔取整後實際漲幅常落在 9.5~10%）
LIMIT_UP_MA_PERIOD = 20
LIMIT_UP_MA_UP_DAYS = 4  # 4 次上升 → 需 5 個 MA20 值
DAY1_OFFSET = 3  # day1 = signal_idx - 3
DAY2_MAX_VOL_MULT = 2.0  # day2 量 < 2× day1 量（嚴格 <）
TAKE_PROFIT_PCT = 20.0  # 停利：進場價 +20%（回測時依進場價計算）
LIMIT_UP_MIN_VOLUME = 1_000_000  # 流動性/真漲停門檻（排除無 10% 漲停限制的商品誤判）

_MA_LOOKBACK = LIMIT_UP_MA_UP_DAYS + 1  # 5 個 MA20 值
_MIN_BARS_NEEDED = LIMIT_UP_MA_PERIOD + DAY1_OFFSET + _MA_LOOKBACK  # 20 + 3 + 5 = 28


@dataclass
class LimitUpContractionResult:
    stock_code: str
    signal_date: pd.Timestamp
    close: float
    volume: float
    day1_date: pd.Timestamp
    day1_high: float
    day1_low: float
    day1_volume: float
    day1_gain_pct: float
    day2_volume: float
    day3_volume: float
    day4_volume: float
    contraction_ratio: float
    stop_loss_price: float  # = day1_low，供回測停損使用
    take_profit_pct: float  # = TAKE_PROFIT_PCT，供回測停利使用
    take_profit_ref_price: float  # 顯示用：day4 收盤 ×1.2（實際停利依進場價）
    review_notes: List[str] = field(default_factory=list)


@dataclass
class LimitUpContractionScanOutput:
    results: List[LimitUpContractionResult]
    price_data: Dict[str, pd.DataFrame]
    scan_date: date
    is_trading_day: bool


def evaluate_limit_up_contraction(
    df: pd.DataFrame, stock_code: str = ""
) -> Optional[LimitUpContractionResult]:
    if len(df) < _MIN_BARS_NEEDED:
        return None

    signal_idx = len(df) - 1
    day4 = df.iloc[signal_idx]
    day3 = df.iloc[signal_idx - 1]
    day2 = df.iloc[signal_idx - 2]
    day1 = df.iloc[signal_idx - 3]
    day1_prev = df.iloc[signal_idx - 4]

    # 量能防呆 + 流動性/真漲停門檻
    day1_vol = float(day1["volume"])
    day2_vol = float(day2["volume"])
    day3_vol = float(day3["volume"])
    day4_vol = float(day4["volume"])
    if any(pd.isna(v) or v <= 0 for v in (day1_vol, day2_vol, day3_vol, day4_vol)):
        return None
    if day1_vol < LIMIT_UP_MIN_VOLUME:
        return None

    # 規則 1a：day1 漲停
    gain = daily_gain_pct(day1, day1_prev)
    if gain < LIMIT_UP_MIN_GAIN_PCT:
        return None

    # 規則 1b：漲停前 5 天 MA20 嚴格逐日遞增（day1-5 … day1-1，5 值）
    ma_slice = df["ma20"].iloc[signal_idx - 8 : signal_idx - 3]
    if ma_slice.isna().any():
        return None
    if not (ma_slice.diff().dropna() > 0).all():
        return None

    # 規則 2：day2 收陰線 + 量 < 2× day1
    if not (float(day2["close"]) < float(day2["open"])):
        return None
    if not (day2_vol < DAY2_MAX_VOL_MULT * day1_vol):
        return None

    day1_high = float(day1["high"])
    day1_low = float(day1["low"])

    # 規則 3：day3 量 < day2 且 收盤 <= day1 高
    if not (day3_vol < day2_vol):
        return None
    if not (float(day3["close"]) <= day1_high):
        return None

    # 規則 4：day4 量 < day3 且 收盤 <= day1 高
    if not (day4_vol < day3_vol):
        return None
    if not (float(day4["close"]) <= day1_high):
        return None

    # 規則 5：day2/3/4 收盤 >= day1 低（day2 刻意無上限）
    if not (
        float(day2["close"]) >= day1_low
        and float(day3["close"]) >= day1_low
        and float(day4["close"]) >= day1_low
    ):
        return None

    day1_date = df.index[signal_idx - 3]
    day4_close = float(day4["close"])
    contraction_ratio = day4_vol / day1_vol
    tp_ref_price = round(day4_close * (1 + TAKE_PROFIT_PCT / 100), 2)

    notes = [
        f"漲停 {day1_date.date()} +{gain:.1f}%（量 {day1_vol:,.0f}），前 5 日 MA20 遞增",
        f"量縮：day2 {day2_vol:,.0f}（<2×day1）→ day3 {day3_vol:,.0f} → day4 {day4_vol:,.0f}"
        f"（day4/day1={contraction_ratio:.2f}）",
        f"整理收在 day1 區間 [{day1_low:.2f}, {day1_high:.2f}] 內",
        f"停損：收盤跌破 {day1_low:.2f}（day1 低）| 停利：進場 +{TAKE_PROFIT_PCT:.0f}%",
    ]

    return LimitUpContractionResult(
        stock_code=stock_code,
        signal_date=df.index[signal_idx],
        close=day4_close,
        volume=day4_vol,
        day1_date=day1_date,
        day1_high=round(day1_high, 2),
        day1_low=round(day1_low, 2),
        day1_volume=day1_vol,
        day1_gain_pct=gain,
        day2_volume=day2_vol,
        day3_volume=day3_vol,
        day4_volume=day4_vol,
        contraction_ratio=round(contraction_ratio, 3),
        stop_loss_price=round(day1_low, 2),
        take_profit_pct=TAKE_PROFIT_PCT,
        take_profit_ref_price=tp_ref_price,
        review_notes=notes,
    )


def _process_limit_up_contraction_stock(
    stock_code: str,
    fetcher: PriceFetcher,
    end_date: Optional[date] = None,
) -> Tuple[str, Optional[LimitUpContractionResult], Optional[pd.DataFrame]]:
    df = fetcher.fetch(stock_code, end_date=end_date)
    if df is None:
        return stock_code, None, None

    df = add_moving_averages(df)
    result = evaluate_limit_up_contraction(df, stock_code=stock_code)
    if result is None:
        return stock_code, None, df
    return stock_code, result, df


def sort_limit_up_contraction_results(
    results: List[LimitUpContractionResult],
) -> List[LimitUpContractionResult]:
    # 量縮最乾（day4/day1 最小）者最前；漲幅大者次之
    return sorted(results, key=lambda r: (r.contraction_ratio, -r.day1_gain_pct))


def scan_limit_up_contraction(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    end_date: Optional[date] = None,
    trading_day: Optional[bool] = None,
) -> LimitUpContractionScanOutput:
    run: ScanRun[LimitUpContractionResult] = run_market_scan(
        partial(_process_limit_up_contraction_stock, end_date=end_date),
        max_workers=max_workers,
        stock_limit=stock_limit,
        end_date=end_date,
        desc="漲停量縮整理掃描",
        trading_day=trading_day,
    )

    results = sort_limit_up_contraction_results(run.results)
    logger.info("漲停量縮整理掃描：%d 檔符合 / %d 檔", len(results), run.total_codes)

    return LimitUpContractionScanOutput(
        results=results,
        price_data=run.price_data,
        scan_date=run.scan_date,
        is_trading_day=run.is_trading_day,
    )
