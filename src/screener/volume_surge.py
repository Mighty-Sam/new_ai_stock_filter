"""爆量價穩選股 — 量增價穩獨立策略。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.data.price_fetcher import PriceFetcher
from src.indicators.moving_average import add_moving_averages
from src.screener.params import V1_PARAMS
from src.screener.scan_runner import ScanRun, run_market_scan
from src.screener.strategy_consolidation import MaTouch

logger = logging.getLogger(__name__)

MA_TOUCH_LABELS = {"ma5": "MA5", "ma10": "MA10", "ma20": "MA20"}
MA_TOUCH_BREAK_TOLERANCE = V1_PARAMS.touch_tolerance

VOLUME_SURGE_MIN_RATIO = 4.0
VOLUME_SURGE_MIN_GAIN_PCT = 0.0
VOLUME_SURGE_MAX_GAIN_PCT = 3.0
VOLUME_SURGE_MIN_SHARES = 500_000
MIN_BARS = 60


@dataclass
class VolumeSurgeResult:
    stock_code: str
    signal_date: pd.Timestamp
    close: float
    daily_gain_pct: float
    volume: float
    vol_ratio_5d: float
    vol_ratio_20d: float
    ma60: float
    touched_ma: MaTouch
    review_notes: List[str] = field(default_factory=list)


@dataclass
class VolumeSurgeScanOutput:
    results: List[VolumeSurgeResult]
    price_data: Dict[str, pd.DataFrame]
    scan_date: date
    is_trading_day: bool


def daily_gain_pct(row: pd.Series, prev_row: pd.Series) -> float:
    prev_close = float(prev_row["close"])
    if prev_close <= 0:
        return 0.0
    return round((float(row["close"]) - prev_close) / prev_close * 100, 2)


def volume_ratio_vs_avg(df: pd.DataFrame, signal_idx: int, period: int) -> float:
    start = signal_idx - period
    if start < 0:
        return 0.0
    window = df.iloc[start:signal_idx]["volume"]
    avg = window.mean()
    if pd.isna(avg) or avg <= 0:
        return 0.0
    return round(float(df.iloc[signal_idx]["volume"]) / float(avg), 2)


def format_touched_ma_label(touched_ma: MaTouch) -> str:
    return MA_TOUCH_LABELS[touched_ma]


def find_volume_surge_ma_touch(
    row: pd.Series,
    break_tolerance: float = MA_TOUCH_BREAK_TOLERANCE,
) -> Optional[MaTouch]:
    """低點須碰觸 MA5/10/20 至少一條；允許下影略破（低點 ≤ 均線 × (1+容差)）。"""
    low = float(row["low"])
    high = float(row["high"])
    touched: List[tuple[MaTouch, float]] = []

    for ma_col in ("ma5", "ma10", "ma20"):
        ma_val = row.get(ma_col)
        if ma_val is None or pd.isna(ma_val):
            continue
        ma = float(ma_val)
        if ma <= 0 or ma > high:
            continue
        if low <= ma * (1 + break_tolerance):
            touched.append((ma_col, ma))  # type: ignore[arg-type]

    if not touched:
        return None
    return max(touched, key=lambda item: item[1])[0]


def evaluate_volume_surge(
    df: pd.DataFrame,
    stock_code: str = "",
) -> Optional[VolumeSurgeResult]:
    """單檔爆量價穩判定。"""
    if len(df) < MIN_BARS:
        return None

    signal_idx = len(df) - 1
    if signal_idx < 1:
        return None

    row = df.iloc[signal_idx]
    prev = df.iloc[signal_idx - 1]

    if float(row["volume"]) < VOLUME_SURGE_MIN_SHARES:
        return None

    gain = daily_gain_pct(row, prev)
    if gain < VOLUME_SURGE_MIN_GAIN_PCT or gain >= VOLUME_SURGE_MAX_GAIN_PCT:
        return None

    ma60 = float(row["ma60"])
    if pd.isna(ma60) or float(row["close"]) <= ma60:
        return None

    ratio_5d = volume_ratio_vs_avg(df, signal_idx, 5)
    ratio_20d = volume_ratio_vs_avg(df, signal_idx, 20)
    if ratio_5d < VOLUME_SURGE_MIN_RATIO or ratio_20d < VOLUME_SURGE_MIN_RATIO:
        return None

    touched_ma = find_volume_surge_ma_touch(row)
    if touched_ma is None:
        return None

    touch_label = format_touched_ma_label(touched_ma)
    sign = "+" if gain >= 0 else ""
    notes = [
        f"量能 {ratio_5d:.1f}×（5日）/ {ratio_20d:.1f}×（20日）",
        f"當日漲幅 {sign}{gain:.2f}% | 收盤 > MA60（{ma60:.2f}）",
        f"低點 ≤ {touch_label}（允許略破）",
    ]

    return VolumeSurgeResult(
        stock_code=stock_code,
        signal_date=df.index[signal_idx],
        close=float(row["close"]),
        daily_gain_pct=gain,
        volume=float(row["volume"]),
        vol_ratio_5d=ratio_5d,
        vol_ratio_20d=ratio_20d,
        ma60=ma60,
        touched_ma=touched_ma,
        review_notes=notes,
    )


def _process_volume_surge_stock(
    stock_code: str,
    fetcher: PriceFetcher,
    end_date: Optional[date] = None,
) -> Tuple[str, Optional[VolumeSurgeResult], Optional[pd.DataFrame]]:
    df = fetcher.fetch(stock_code, end_date=end_date)
    if df is None:
        return stock_code, None, None

    df = add_moving_averages(df)
    result = evaluate_volume_surge(df, stock_code=stock_code)
    if result is None:
        return stock_code, None, df
    return stock_code, result, df


def sort_volume_surge_results(results: List[VolumeSurgeResult]) -> List[VolumeSurgeResult]:
    return sorted(
        results,
        key=lambda r: min(r.vol_ratio_5d, r.vol_ratio_20d),
        reverse=True,
    )


def scan_volume_surge(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    end_date: Optional[date] = None,
    trading_day: Optional[bool] = None,
) -> VolumeSurgeScanOutput:
    run: ScanRun[VolumeSurgeResult] = run_market_scan(
        partial(_process_volume_surge_stock, end_date=end_date),
        max_workers=max_workers,
        stock_limit=stock_limit,
        end_date=end_date,
        desc="爆量價穩掃描",
        trading_day=trading_day,
    )

    results = sort_volume_surge_results(run.results)
    logger.info("爆量價穩掃描：%d 檔符合 / %d 檔", len(results), run.total_codes)

    return VolumeSurgeScanOutput(
        results=results,
        price_data=run.price_data,
        scan_date=run.scan_date,
        is_trading_day=run.is_trading_day,
    )
