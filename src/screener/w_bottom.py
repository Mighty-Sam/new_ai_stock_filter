"""N漲W底假跌破選股 — 前波急漲後回測形成 W 底，第二腳假跌破再收復。"""

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

logger = logging.getLogger(__name__)

W_BOTTOM_LOOKBACK = 50
W_BOTTOM_MIN_RALLY_PCT = 20.0
W_BOTTOM_MAX_RALLY_PCT = 45.0
W_BOTTOM_MIN_VOLUME = 1_000_000
W_BOTTOM_MIN_PIERCE_PCT = 2.0
W_BOTTOM_MIN_TP_UPSIDE_PCT = 5.0
WEEKLY_MA_PERIOD = 20
WEEKLY_MA_UP_WEEKS = 4  # 連續 4 週遞增（最新 5 個週線 MA20 值逐週上升）＝原始設計；放寬至 2 週經回測驗證會引進弱勢股


@dataclass
class WBottomResult:
    stock_code: str
    signal_date: pd.Timestamp
    close: float
    volume: float
    rally_pct: float
    peak_high: float
    leg1_low: float
    leg1_date: pd.Timestamp
    rebound_high: float
    leg2_low: float
    pierce_pct: float
    tp_upside_pct: float
    take_profit_price: float
    stop_loss_price: float
    review_notes: List[str] = field(default_factory=list)


@dataclass
class WBottomScanOutput:
    results: List[WBottomResult]
    price_data: Dict[str, pd.DataFrame]
    scan_date: date
    is_trading_day: bool


def weekly_ma20_trending_up(
    df: pd.DataFrame,
    as_of: pd.Timestamp,
    period: int = WEEKLY_MA_PERIOD,
    up_weeks: int = WEEKLY_MA_UP_WEEKS,
) -> bool:
    """以週線收盤計算 MA20，檢查是否連續 up_weeks 週遞增（需 up_weeks+1 個週值）。"""
    subset = df.loc[:as_of]
    if subset.empty:
        return False

    weekly = subset["close"].resample("W-FRI").last().dropna()
    weekly_ma = weekly.rolling(window=period, min_periods=period).mean().dropna()
    if len(weekly_ma) < up_weeks + 1:
        return False

    recent = weekly_ma.iloc[-(up_weeks + 1):]
    return bool((recent.diff().dropna() > 0).all())


def _second_highest_high(df: pd.DataFrame, signal_idx: int, lookback: int = W_BOTTOM_LOOKBACK) -> float:
    start = max(0, signal_idx - lookback + 1)
    highs = df.iloc[start : signal_idx + 1]["high"]
    top2 = highs.nlargest(2)
    return float(top2.iloc[-1])


def _find_w_bottom(df: pd.DataFrame, signal_idx: int, lookback: int = W_BOTTOM_LOOKBACK) -> Optional[dict]:
    """在 lookback 根 K 棒視窗內尋找「急漲 → 第一腳 → 反彈 → 第二腳假跌破（今日）」結構。"""
    window_start = max(0, signal_idx - lookback + 1)
    if signal_idx - window_start < 3:
        return None

    # 1) 找波段高點（今日之前），並反推波段起漲低點，檢查漲幅落在門檻區間
    pre_today = df.iloc[window_start:signal_idx]
    if pre_today.empty:
        return None
    peak_pos = int(pre_today["high"].to_numpy().argmax())
    peak_idx = window_start + peak_pos
    if peak_idx <= window_start:
        return None

    pre_peak = df.iloc[window_start : peak_idx + 1]
    rally_low = float(pre_peak["low"].min())
    peak_high = float(df.iloc[peak_idx]["high"])
    if rally_low <= 0:
        return None
    rally_pct = (peak_high - rally_low) / rally_low * 100
    if not (W_BOTTOM_MIN_RALLY_PCT <= rally_pct <= W_BOTTOM_MAX_RALLY_PCT):
        return None

    # 2) 第一腳：波段高點後、今日之前的最低點
    if peak_idx >= signal_idx - 1:
        return None
    pullback_zone = df.iloc[peak_idx + 1 : signal_idx]
    leg1_pos = int(pullback_zone["low"].to_numpy().argmin())
    leg1_idx = peak_idx + 1 + leg1_pos
    leg1_low = float(df.iloc[leg1_idx]["low"])
    leg1_date = df.index[leg1_idx]

    # 3) 反彈：第一腳後、今日之前的最高點，須真正彈過第一腳低點才算成形的 W
    if leg1_idx >= signal_idx - 1:
        return None
    rebound_zone = df.iloc[leg1_idx + 1 : signal_idx]
    rebound_high = float(rebound_zone["high"].max())
    if rebound_high <= leg1_low:
        return None

    # 4) 第二腳（今日）：低點跌破第一腳但收盤收復，且跌破幅度需達最低門檻（避免只是碰一下的雜訊）
    today = df.iloc[signal_idx]
    today_low = float(today["low"])
    today_close = float(today["close"])
    if today_low >= leg1_low:
        return None
    if today_close < leg1_low:
        return None

    pierce_pct = (leg1_low - today_low) / leg1_low * 100
    if pierce_pct < W_BOTTOM_MIN_PIERCE_PCT:
        return None

    return {
        "peak_idx": peak_idx,
        "peak_high": peak_high,
        "rally_pct": rally_pct,
        "leg1_idx": leg1_idx,
        "leg1_low": leg1_low,
        "leg1_date": leg1_date,
        "rebound_high": rebound_high,
        "leg2_low": today_low,
        "pierce_pct": pierce_pct,
    }


def evaluate_w_bottom(df: pd.DataFrame, stock_code: str = "") -> Optional[WBottomResult]:
    min_bars_needed = max(W_BOTTOM_LOOKBACK, (WEEKLY_MA_PERIOD + WEEKLY_MA_UP_WEEKS + 1) * 5)
    if len(df) < min_bars_needed:
        return None

    signal_idx = len(df) - 1
    row = df.iloc[signal_idx]

    if float(row["volume"]) < W_BOTTOM_MIN_VOLUME:
        return None

    pattern = _find_w_bottom(df, signal_idx)
    if pattern is None:
        return None

    if not weekly_ma20_trending_up(df, df.index[signal_idx]):
        return None

    take_profit = _second_highest_high(df, signal_idx)
    stop_loss = pattern["leg2_low"]

    # 停利空間過小的訊號賺賠比太差（回測顯示 <5% 這批均報酬近乎打平），直接跳過
    close = float(row["close"])
    tp_upside_pct = (take_profit - close) / close * 100
    if tp_upside_pct < W_BOTTOM_MIN_TP_UPSIDE_PCT:
        return None

    notes = [
        f"前波漲幅 {pattern['rally_pct']:.1f}%（近 {W_BOTTOM_LOOKBACK} K 內）",
        f"W底：第一腳 {pattern['leg1_low']:.2f}（{pattern['leg1_date'].date()}）"
        f" → 反彈高 {pattern['rebound_high']:.2f} → 第二腳假跌破 {pattern['leg2_low']:.2f}"
        f"（跌破 {pattern['pierce_pct']:.1f}%）",
        f"停利：{take_profit:.2f}（+{tp_upside_pct:.1f}%，{W_BOTTOM_LOOKBACK}K 次高點）"
        f" | 停損：收盤跌破 {stop_loss:.2f}",
    ]

    return WBottomResult(
        stock_code=stock_code,
        signal_date=df.index[signal_idx],
        close=close,
        volume=float(row["volume"]),
        rally_pct=round(pattern["rally_pct"], 2),
        peak_high=round(pattern["peak_high"], 2),
        leg1_low=round(pattern["leg1_low"], 2),
        leg1_date=pattern["leg1_date"],
        rebound_high=round(pattern["rebound_high"], 2),
        leg2_low=round(pattern["leg2_low"], 2),
        pierce_pct=round(pattern["pierce_pct"], 2),
        tp_upside_pct=round(tp_upside_pct, 2),
        take_profit_price=round(take_profit, 2),
        stop_loss_price=round(stop_loss, 2),
        review_notes=notes,
    )


def _process_w_bottom_stock(
    stock_code: str,
    fetcher: PriceFetcher,
    end_date: Optional[date] = None,
) -> Tuple[str, Optional[WBottomResult], Optional[pd.DataFrame]]:
    df = fetcher.fetch(stock_code, end_date=end_date)
    if df is None:
        return stock_code, None, None

    df = add_moving_averages(df)
    result = evaluate_w_bottom(df, stock_code=stock_code)
    if result is None:
        return stock_code, None, df
    return stock_code, result, df


def sort_w_bottom_results(results: List[WBottomResult]) -> List[WBottomResult]:
    return sorted(results, key=lambda r: r.rally_pct, reverse=True)


def scan_w_bottom(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    end_date: Optional[date] = None,
    trading_day: Optional[bool] = None,
) -> WBottomScanOutput:
    run: ScanRun[WBottomResult] = run_market_scan(
        partial(_process_w_bottom_stock, end_date=end_date),
        max_workers=max_workers,
        stock_limit=stock_limit,
        end_date=end_date,
        desc="N漲W底假跌破掃描",
        trading_day=trading_day,
    )

    results = sort_w_bottom_results(run.results)
    logger.info("N漲W底假跌破掃描：%d 檔符合 / %d 檔", len(results), run.total_codes)

    return WBottomScanOutput(
        results=results,
        price_data=run.price_data,
        scan_date=run.scan_date,
        is_trading_day=run.is_trading_day,
    )
