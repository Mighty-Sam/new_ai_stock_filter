"""近 3 年歷史回測。"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd
from tqdm import tqdm

from src.backtest.stats import BacktestSummary, PeriodStats, aggregate_trades
from src.backtest.trade_simulator import TradeResult, simulate_trades
from src.data.benchmark import fetch_benchmark
from src.data.price_fetcher import PriceFetcher
from src.data.stock_list import get_stock_list
from src.indicators.moving_average import add_moving_averages
from src.screener.conditions import evaluate_as_of

logger = logging.getLogger(__name__)

HISTORY_YEARS = 3
SUMMARY_CACHE_PATH = Path("data/backtest_summary.json")
TRADES_CSV_PATH = Path("data/backtest_trades.csv")
CACHE_TTL_HOURS = 24
MIN_WARMUP = 120
MIN_FORWARD = 20


def _cache_fresh(path: Path, ttl_hours: int = CACHE_TTL_HOURS) -> bool:
    if not path.exists():
        return False
    age = pd.Timestamp.now() - pd.Timestamp(path.stat().st_mtime, unit="s")
    return age.total_seconds() < ttl_hours * 3600


def _summary_from_dict(data: dict) -> BacktestSummary:
    period_stats = [
        PeriodStats(
            hold_days=ps["hold_days"],
            label=ps.get("label"),
            sample_count=ps["sample_count"],
            win_rate=ps["win_rate"],
            avg_return_pct=ps["avg_return_pct"],
            median_return_pct=ps["median_return_pct"],
            beat_benchmark_rate=ps["beat_benchmark_rate"],
            avg_alpha_pct=ps["avg_alpha_pct"],
        )
        for ps in data.get("period_stats", [])
    ]
    return BacktestSummary(
        source=data.get("source", "historical"),
        period_stats=period_stats,
        updated_at=data.get("updated_at"),
        from_cache=True,
        stocks_scanned=data.get("stocks_scanned", 0),
        stocks_with_data=data.get("stocks_with_data", 0),
        signal_count=data.get("signal_count", 0),
    )


def _summary_trustworthy(summary: BacktestSummary) -> bool:
    """舊快取或資料覆蓋不足時視為不可信，應重跑回測。"""
    max_n = max((ps.sample_count for ps in summary.period_stats), default=0)
    if not summary.period_stats:
        return False
    if summary.stocks_scanned == 0:
        return max_n >= 50
    coverage = (
        summary.stocks_with_data / summary.stocks_scanned
        if summary.stocks_scanned
        else 0
    )
    return coverage >= 0.3 and max_n >= 20


def _summary_to_dict(summary: BacktestSummary) -> dict:
    return {
        "source": summary.source,
        "updated_at": summary.updated_at,
        "stocks_scanned": summary.stocks_scanned,
        "stocks_with_data": summary.stocks_with_data,
        "signal_count": summary.signal_count,
        "period_stats": [
            {
                "hold_days": ps.hold_days,
                "label": ps.label,
                "sample_count": ps.sample_count,
                "win_rate": ps.win_rate,
                "avg_return_pct": ps.avg_return_pct,
                "median_return_pct": ps.median_return_pct,
                "beat_benchmark_rate": ps.beat_benchmark_rate,
                "avg_alpha_pct": ps.avg_alpha_pct,
            }
            for ps in summary.period_stats
        ],
    }


def load_cached_summary() -> Optional[BacktestSummary]:
    if not SUMMARY_CACHE_PATH.exists():
        return None
    try:
        with SUMMARY_CACHE_PATH.open(encoding="utf-8") as f:
            return _summary_from_dict(json.load(f))
    except Exception as exc:
        logger.warning("讀取回測快取失敗: %s", exc)
        return None


def save_summary(summary: BacktestSummary) -> None:
    SUMMARY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SUMMARY_CACHE_PATH.open("w", encoding="utf-8") as f:
        json.dump(_summary_to_dict(summary), f, ensure_ascii=False, indent=2)


def save_trades_csv(trades: List[TradeResult]) -> None:
    if not trades:
        return
    TRADES_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "stock_code": t.stock_code,
            "signal_date": t.signal_date.isoformat(),
            "entry_date": t.entry_date.isoformat(),
            "entry_price": t.entry_price,
            "exit_date": t.exit_date.isoformat(),
            "exit_price": t.exit_price,
            "hold_days": t.hold_days,
            "exit_reason": t.exit_reason,
            "return_pct": t.return_pct,
            "benchmark_return_pct": t.benchmark_return_pct,
            "alpha_pct": t.alpha_pct,
            "is_win": t.is_win,
            "beat_benchmark": t.beat_benchmark,
        }
        for t in trades
    ]
    pd.DataFrame(rows).to_csv(TRADES_CSV_PATH, index=False, encoding="utf-8-sig")


def _backtest_single_stock(
    stock_code: str,
    benchmark_df: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> tuple[List[TradeResult], bool]:
    fetcher = PriceFetcher(delay=0.05)
    days = (end_date - start_date).days + MIN_WARMUP + MIN_FORWARD + 60
    df = fetcher.fetch(
        stock_code,
        days=days,
        end_date=end_date,
        min_rows=MIN_WARMUP + MIN_FORWARD + 1,
    )
    if df is None or len(df) < MIN_WARMUP + MIN_FORWARD + 1:
        return [], False

    df = add_moving_averages(df)
    trades: List[TradeResult] = []

    for i in range(MIN_WARMUP, len(df) - MIN_FORWARD):
        as_of = df.index[i]
        if as_of.date() < start_date:
            continue
        if as_of.date() > end_date:
            break

        result = evaluate_as_of(df, as_of, stock_code=stock_code)
        if result is None:
            continue

        signal_date = as_of.date()
        batch = simulate_trades(stock_code, df, benchmark_df, signal_date)
        trades.extend(batch)

    return trades, True


def run_historical_backtest(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
) -> BacktestSummary:
    end_date = date.today()
    start_date = end_date - timedelta(days=365 * HISTORY_YEARS)

    benchmark_df = fetch_benchmark()
    stocks = get_stock_list()
    codes = sorted(stocks.keys())
    if stock_limit:
        codes = codes[:stock_limit]

    all_trades: List[TradeResult] = []
    stocks_with_data = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _backtest_single_stock, code, benchmark_df, start_date, end_date
            ): code
            for code in codes
        }
        for future in tqdm(as_completed(futures), total=len(futures), desc="歷史回測"):
            try:
                trades, had_data = future.result()
                if had_data:
                    stocks_with_data += 1
                all_trades.extend(trades)
            except Exception as exc:
                logger.debug("回測失敗 %s: %s", futures[future], exc)

    signal_count = len(
        {(t.stock_code, t.signal_date) for t in all_trades if t.valid}
    )
    summary = aggregate_trades(all_trades, source="historical")
    summary.stocks_scanned = len(codes)
    summary.stocks_with_data = stocks_with_data
    summary.signal_count = signal_count
    save_summary(summary)
    save_trades_csv(all_trades)
    logger.info(
        "歷史回測完成：%d 檔有資料 / %d 檔，%d 信號，%d 筆交易",
        stocks_with_data,
        len(codes),
        signal_count,
        len(all_trades),
    )
    return summary


def get_or_run_backtest(
    refresh: bool = False,
    skip: bool = False,
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
) -> Optional[BacktestSummary]:
    if skip:
        cached = load_cached_summary()
        return cached

    if not refresh and _cache_fresh(SUMMARY_CACHE_PATH):
        cached = load_cached_summary()
        if cached and _summary_trustworthy(cached):
            logger.info("使用歷史回測快取")
            return cached
        if cached:
            logger.warning("回測快取樣本不足或資料覆蓋偏低，重新執行回測")

    try:
        return run_historical_backtest(max_workers=max_workers, stock_limit=stock_limit)
    except RuntimeError as exc:
        logger.error("歷史回測失敗: %s", exc)
        cached = load_cached_summary()
        if cached:
            logger.info("改用歷史回測快取")
            return cached
        return None
