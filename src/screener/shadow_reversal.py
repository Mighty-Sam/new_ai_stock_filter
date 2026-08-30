"""長下影線反轉選股 — 大量長下影線跌破均線與前波低點後的洗盤反轉型態。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.data.benchmark import fetch_benchmark
from src.data.institutional import _row_net_lots, fetch_institutional_wide_range
from src.data.price_fetcher import PriceFetcher
from src.indicators.moving_average import add_moving_averages
from src.screener.scan_runner import ScanRun, run_market_scan
from src.screener.strategy_consolidation import gain_ratio_n

logger = logging.getLogger(__name__)

SHADOW_MIN_AVG_VOLUME_30D = 3_000_000  # 3000 張（股數）；疊加法人連買條件後，3000張的候選池比5000張更划算
SHADOW_LOWER_SHADOW_MIN_RATIO = 0.6  # 下影線 ≥ 全K棒範圍（high-low）的 60%
SHADOW_MA_PERIODS = ("ma5", "ma10", "ma20", "ma60", "ma120")
SHADOW_LOOKBACK_GAIN_BARS = 22
SHADOW_LOOKBACK_MIN_GAIN_PCT = 30.0  # 全市場回測驗證：25%→30% PF由1.92提升至2.15；35%/40%無進一步邊際效益
SHADOW_PRIOR_LOW_LOOKBACK = 5
SHADOW_INSTITUTIONAL_STREAK_LOOKBACK = 10  # 連買天數計算的回看交易日數
SHADOW_INSTITUTIONAL_MIN_STREAK = 2  # 全市場回測驗證：三大法人合計連買≥2日，PF由2.15提升至3.91
TAKE_PROFIT_PCT = 20.0  # 停利：進場價 +20%
MAX_HOLD_DAYS = 20

_MIN_BARS_NEEDED = 120  # MA120 為所有條件中最長的窗口


@dataclass
class ShadowReversalResult:
    stock_code: str
    signal_date: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    lower_shadow_ratio: float  # 下影線 / 全K棒範圍
    touched_mas: List[str]  # 低點跌破的均線列表
    lookback_gain_pct: float  # 往前 N 根K棒最低到最高漲幅%
    prior_low: float  # 前 N 根K棒最低點
    stop_loss_price: float  # = 當根 low，供回測停損使用
    take_profit_pct: float  # = TAKE_PROFIT_PCT，供回測停利使用
    volume_increased: bool  # 當根量 > 前一根量（選用參考，非篩選門檻）
    market_trend_ok: Optional[bool] = None  # 訊號當日 0050 收盤 > 自身MA20；未提供大盤資料時為 None（不檢查）
    institutional_streak: Optional[int] = None  # 訊號當日往前算，三大法人合計連續買超天數；未提供籌碼資料時為 None（不檢查）
    review_notes: List[str] = field(default_factory=list)


@dataclass
class ShadowReversalScanOutput:
    results: List[ShadowReversalResult]
    price_data: Dict[str, pd.DataFrame]
    scan_date: date
    is_trading_day: bool


def _lower_shadow_ratio(row: pd.Series) -> float:
    """下影線長度 / 全K棒範圍（high-low）。"""
    open_p = float(row["open"])
    high_p = float(row["high"])
    low_p = float(row["low"])
    close_p = float(row["close"])
    full_range = high_p - low_p
    if full_range <= 0:
        return 0.0
    lower_shadow = min(open_p, close_p) - low_p
    return max(lower_shadow, 0.0) / full_range


def _touched_mas(row: pd.Series, periods: Tuple[str, ...] = SHADOW_MA_PERIODS) -> List[str]:
    """回傳低點跌破（低於）的均線名稱列表。"""
    low_p = float(row["low"])
    touched: List[str] = []
    for ma_col in periods:
        ma_val = row.get(ma_col)
        if ma_val is None or pd.isna(ma_val):
            continue
        if low_p < float(ma_val):
            touched.append(ma_col)
    return touched


def _broke_prior_low(
    df: pd.DataFrame, signal_idx: int, lookback: int = SHADOW_PRIOR_LOW_LOOKBACK
) -> Tuple[bool, float]:
    """當根低點是否跌破前 lookback 根（不含當根）K棒的最低點。"""
    start = max(0, signal_idx - lookback)
    window = df.iloc[start:signal_idx]
    if window.empty:
        return False, float("nan")
    prior_low = float(window["low"].min())
    current_low = float(df.iloc[signal_idx]["low"])
    return current_low < prior_low, prior_low


def _has_gap_down_in_window(
    df: pd.DataFrame, signal_idx: int, lookback: int = SHADOW_LOOKBACK_GAIN_BARS
) -> bool:
    """N 根K棒範圍內，是否出現向下跳空缺口（後一根最高點 < 前一根最低點，完全無重疊）。"""
    start = max(0, signal_idx - lookback + 1)
    window = df.iloc[start : signal_idx + 1]
    prev_low = window["low"].shift(1)
    gap_down = window["high"] < prev_low
    return bool(gap_down.fillna(False).any())


def _benchmark_trend_ok(benchmark_df: pd.DataFrame, as_of: pd.Timestamp) -> Optional[bool]:
    """訊號當日（或之前最近一個交易日）0050 收盤是否 > 自身MA20，且MA20 > MA60（雙重確認）。
    全市場回測驗證：這條件在多頭期間表現與單純「收盤>MA20」完全相同（PF 2.35，MA20多頭期間幾乎
    全程都在MA60之上，不會多濾掉東西），但在2023震盪年能把PF從1.03拉到1.20——多頭不變差、
    震盪年變好，是低風險的升級。2022真正熊市無論哪種規則都救不了，不在此濾網的設計目標內。
    無資料時回傳 None（不檢查）。"""
    if benchmark_df is None or benchmark_df.empty or "ma20" not in benchmark_df.columns:
        return None
    eligible = benchmark_df[benchmark_df.index <= as_of]
    if eligible.empty:
        return None
    row = eligible.iloc[-1]
    ma20 = row.get("ma20")
    ma60 = row.get("ma60")
    close = row.get("close")
    if any(v is None or pd.isna(v) for v in (ma20, ma60, close)):
        return None
    return bool(float(close) > float(ma20) and float(ma20) > float(ma60))


def prepare_benchmark_for_shadow_reversal(days: int = 1200) -> pd.DataFrame:
    """取得 0050 並補上 MA20/MA60，供 evaluate_shadow_reversal 的大盤濾網使用。"""
    benchmark_df = fetch_benchmark(days=days)
    benchmark_df = benchmark_df.copy()
    benchmark_df["ma20"] = benchmark_df["close"].rolling(window=20, min_periods=20).mean()
    benchmark_df["ma60"] = benchmark_df["close"].rolling(window=60, min_periods=60).mean()
    return benchmark_df


def _institutional_streak(
    institutional_df: Optional[pd.DataFrame],
    as_of: pd.Timestamp,
    lookback: int = SHADOW_INSTITUTIONAL_STREAK_LOOKBACK,
) -> Optional[int]:
    """訊號當日（或之前最近一個交易日）往前算，三大法人合計連續買超天數。無資料時回傳 None。"""
    if institutional_df is None or institutional_df.empty:
        return None

    work = institutional_df.copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work[work["date"] <= as_of].sort_values("date").tail(lookback)
    if work.empty:
        return None

    totals = [_row_net_lots(row)[3] for _, row in work.iterrows()]
    streak = 0
    for value in reversed(totals):
        if value > 0:
            streak += 1
        else:
            break
    return streak


def prepare_institutional_for_shadow_reversal(
    stock_code: str,
    end_date: date,
    lookback_days: int = 400,
) -> Optional[pd.DataFrame]:
    """抓取單檔法人買賣資料，供 evaluate_shadow_reversal 的連買濾網使用（一次抓完整區間，避免逐日重複呼叫）。"""
    from datetime import timedelta

    start = end_date - timedelta(days=lookback_days)
    return fetch_institutional_wide_range(stock_code, start_date=start, end_date=end_date)


def evaluate_shadow_reversal(
    df: pd.DataFrame,
    stock_code: str = "",
    benchmark_df: Optional[pd.DataFrame] = None,
    institutional_df: Optional[pd.DataFrame] = None,
) -> Optional[ShadowReversalResult]:
    if len(df) < _MIN_BARS_NEEDED:
        return None

    signal_idx = len(df) - 1
    row = df.iloc[signal_idx]

    open_p = float(row["open"])
    high_p = float(row["high"])
    low_p = float(row["low"])
    close_p = float(row["close"])
    volume = float(row["volume"])
    if any(pd.isna(v) or v <= 0 for v in (open_p, high_p, low_p, close_p, volume)):
        return None

    # 條件1：30日均量門檻
    avg_volume_30d = float(df["volume"].tail(30).mean())
    if pd.isna(avg_volume_30d) or avg_volume_30d < SHADOW_MIN_AVG_VOLUME_30D:
        return None

    # 條件2：長下影線（下影線 ≥ 全K棒範圍門檻比例）
    shadow_ratio = _lower_shadow_ratio(row)
    if shadow_ratio < SHADOW_LOWER_SHADOW_MIN_RATIO:
        return None

    # 條件3：低點跌破 MA5/10/20/60/120 中至少一條
    touched = _touched_mas(row)
    if not touched:
        return None

    # 條件4：往前 22 根K棒（含當根）最低到最高漲幅 > 25%
    lookback_gain_pct = gain_ratio_n(df, signal_idx, SHADOW_LOOKBACK_GAIN_BARS) * 100
    if lookback_gain_pct <= SHADOW_LOOKBACK_MIN_GAIN_PCT:
        return None

    # 條件5：當根低點跌破前5根K棒最低點
    broke_prior_low, prior_low = _broke_prior_low(df, signal_idx)
    if not broke_prior_low:
        return None

    # 條件8：訊號K棒收盤價 > 自身 MA20（守住短期均線，非單純破底）
    ma20 = row.get("ma20")
    if ma20 is None or pd.isna(ma20) or close_p <= float(ma20):
        return None

    # 條件9：往前 22 根K棒範圍內不能出現向下跳空缺口（缺口代表趨勢偏空，非乾淨拉回）
    if _has_gap_down_in_window(df, signal_idx, SHADOW_LOOKBACK_GAIN_BARS):
        return None

    # 條件10（大盤濾網，選用）：訊號當日 0050 收盤須站上自身 MA20，避開大盤本身偏空的期間
    market_trend_ok = _benchmark_trend_ok(benchmark_df, df.index[signal_idx])
    if market_trend_ok is False:
        return None

    # 條件11（法人籌碼濾網，選用）：三大法人合計連買天數須達門檻
    institutional_streak = _institutional_streak(institutional_df, df.index[signal_idx])
    if institutional_streak is not None and institutional_streak < SHADOW_INSTITUTIONAL_MIN_STREAK:
        return None

    # 條件6（選用，僅供人工複核參考，非篩選門檻）：當根量能是否大於前一根
    prev_volume = float(df.iloc[signal_idx - 1]["volume"]) if signal_idx >= 1 else None
    volume_increased = prev_volume is not None and volume > prev_volume

    stop_loss_price = round(low_p, 2)
    notes = [
        f"長下影線：下影線佔全距 {shadow_ratio * 100:.1f}%"
        f"（O={open_p:.2f} H={high_p:.2f} L={low_p:.2f} C={close_p:.2f}）",
        f"低點跌破 {'/'.join(m.upper() for m in touched)}",
        f"往前{SHADOW_LOOKBACK_GAIN_BARS}根K棒漲幅 {lookback_gain_pct:.1f}%"
        f"（門檻 >{SHADOW_LOOKBACK_MIN_GAIN_PCT:.0f}%）",
        f"跌破前{SHADOW_PRIOR_LOW_LOOKBACK}根低點 {prior_low:.2f}",
        f"收盤 {close_p:.2f} 守住 MA20（{float(ma20):.2f}）",
        f"往前{SHADOW_LOOKBACK_GAIN_BARS}根K棒內無向下跳空缺口",
        "✅ 量能較前一根放大" if volume_increased else "ℹ️ 量能未較前一根放大（非必要條件）",
        (
            "✅ 0050 站上自身MA20且MA20>MA60（雙重確認）"
            if market_trend_ok
            else "ℹ️ 未提供大盤資料，未檢查大盤趨勢"
            if market_trend_ok is None
            else "⚠️ 0050 未同時滿足站上MA20且MA20>MA60"
        ),
        (
            f"✅ 三大法人合計連買 {institutional_streak} 日"
            if institutional_streak is not None
            else "ℹ️ 未提供法人籌碼資料，未檢查連買天數"
        ),
        f"停損：收盤跌破 {stop_loss_price:.2f}（下影線低點）| 停利：進場 +{TAKE_PROFIT_PCT:.0f}%",
    ]

    return ShadowReversalResult(
        stock_code=stock_code,
        signal_date=df.index[signal_idx],
        open=round(open_p, 2),
        high=round(high_p, 2),
        low=round(low_p, 2),
        close=round(close_p, 2),
        volume=volume,
        lower_shadow_ratio=round(shadow_ratio, 4),
        touched_mas=touched,
        lookback_gain_pct=round(lookback_gain_pct, 2),
        prior_low=round(prior_low, 2),
        stop_loss_price=stop_loss_price,
        take_profit_pct=TAKE_PROFIT_PCT,
        volume_increased=volume_increased,
        market_trend_ok=market_trend_ok,
        institutional_streak=institutional_streak,
        review_notes=notes,
    )


def _process_shadow_reversal_stock(
    stock_code: str,
    fetcher: PriceFetcher,
    end_date: Optional[date] = None,
    benchmark_df: Optional[pd.DataFrame] = None,
) -> Tuple[str, Optional[ShadowReversalResult], Optional[pd.DataFrame]]:
    df = fetcher.fetch(stock_code, end_date=end_date)
    if df is None:
        return stock_code, None, None

    df = add_moving_averages(df)
    institutional_df = prepare_institutional_for_shadow_reversal(
        stock_code, end_date or date.today(), lookback_days=60
    )
    result = evaluate_shadow_reversal(
        df, stock_code=stock_code, benchmark_df=benchmark_df, institutional_df=institutional_df
    )
    if result is None:
        return stock_code, None, df
    return stock_code, result, df


def sort_shadow_reversal_results(
    results: List[ShadowReversalResult],
) -> List[ShadowReversalResult]:
    # 下影線比例越長、往前波段漲幅越大者越前面
    return sorted(results, key=lambda r: (-r.lower_shadow_ratio, -r.lookback_gain_pct))


def scan_shadow_reversal(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    end_date: Optional[date] = None,
    trading_day: Optional[bool] = None,
) -> ShadowReversalScanOutput:
    benchmark_df = prepare_benchmark_for_shadow_reversal()
    run: ScanRun[ShadowReversalResult] = run_market_scan(
        partial(_process_shadow_reversal_stock, end_date=end_date, benchmark_df=benchmark_df),
        max_workers=max_workers,
        stock_limit=stock_limit,
        end_date=end_date,
        desc="長下影線反轉掃描",
        trading_day=trading_day,
    )

    results = sort_shadow_reversal_results(run.results)
    logger.info("長下影線反轉掃描：%d 檔符合 / %d 檔", len(results), run.total_codes)

    return ShadowReversalScanOutput(
        results=results,
        price_data=run.price_data,
        scan_date=run.scan_date,
        is_trading_day=run.is_trading_day,
    )
