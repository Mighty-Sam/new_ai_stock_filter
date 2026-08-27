"""長下影線反轉 歷史回測。

輸出檔名支援 period_tag（比照 sl_tp_backtest.py），避免不同區間的回測互相覆蓋。
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
from tqdm import tqdm

from src.backtest.shadow_reversal_simulator import simulate_shadow_reversal_trade
from src.backtest.stats import BacktestSummary, PeriodStats, aggregate_trades
from src.backtest.trade_simulator import TradeResult
from src.data.benchmark import fetch_benchmark
from src.data.price_fetcher import PriceFetcher
from src.data.stock_list import get_stock_list
from src.indicators.moving_average import add_moving_averages
from src.screener.shadow_reversal import evaluate_shadow_reversal

logger = logging.getLogger(__name__)

STRATEGY_LABEL = "長下影線反轉（進場+20% 停利／收盤跌破下影線低點停損）"
HISTORY_YEARS = 3
CACHE_TTL_HOURS = 24
MIN_WARMUP = 120  # 對齊 evaluate_shadow_reversal 所需的 MA120 暖身根數
MIN_FORWARD = 25  # 供最多 20 根K棒持有的模擬預留緩衝


def output_paths(period_tag: Optional[str] = None) -> Tuple[Path, Path]:
    if period_tag:
        return (
            Path(f"data/shadow_reversal_backtest_summary_{period_tag}.json"),
            Path(f"data/shadow_reversal_backtest_trades_{period_tag}.csv"),
        )
    return (
        Path("data/shadow_reversal_backtest_summary.json"),
        Path("data/shadow_reversal_backtest_trades.csv"),
    )


def resolve_backtest_window(
    history_years: int = HISTORY_YEARS,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Tuple[date, date]:
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=365 * history_years))
    return start, end


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


def load_cached_summary(period_tag: Optional[str] = None) -> Optional[BacktestSummary]:
    summary_path, _ = output_paths(period_tag)
    if not summary_path.exists():
        return None
    try:
        with summary_path.open(encoding="utf-8") as f:
            return _summary_from_dict(json.load(f))
    except Exception as exc:
        logger.warning("讀取長下影線反轉回測快取失敗: %s", exc)
        return None


def save_summary(summary: BacktestSummary, period_tag: Optional[str] = None) -> None:
    summary_path, _ = output_paths(period_tag)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(_summary_to_dict(summary), f, ensure_ascii=False, indent=2)


def save_trades_csv(trades: List[TradeResult], period_tag: Optional[str] = None) -> None:
    if not trades:
        return
    _, trades_path = output_paths(period_tag)
    trades_path.parent.mkdir(parents=True, exist_ok=True)
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
    pd.DataFrame(rows).to_csv(trades_path, index=False, encoding="utf-8-sig")


def _backtest_single_stock(
    stock_code: str,
    benchmark_df: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> Tuple[List[TradeResult], bool]:
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

        subset = df.iloc[: i + 1]
        result = evaluate_shadow_reversal(subset, stock_code=stock_code, benchmark_df=benchmark_df)
        if result is None:
            continue

        signal_date = as_of.date()
        trade = simulate_shadow_reversal_trade(
            stock_code,
            df,
            benchmark_df,
            signal_date,
            stop_loss_price=result.stop_loss_price,
            take_profit_pct=result.take_profit_pct,
        )
        if trade is not None:
            trades.append(trade)

    return trades, True


def run_shadow_reversal_backtest(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    history_years: int = HISTORY_YEARS,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    period_tag: Optional[str] = None,
) -> BacktestSummary:
    explicit_window = start_date is not None or end_date is not None
    start_date, end_date = resolve_backtest_window(history_years, start_date, end_date)
    if period_tag is None and explicit_window:
        period_tag = (
            f"{start_date.year}"
            if start_date.year == end_date.year
            else f"{start_date.year}_{end_date.year}"
        )

    benchmark_df = fetch_benchmark()
    benchmark_df = benchmark_df.copy()
    benchmark_df["ma20"] = benchmark_df["close"].rolling(window=20, min_periods=20).mean()
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
        for future in tqdm(as_completed(futures), total=len(futures), desc="長下影線反轉回測"):
            try:
                trades, had_data = future.result()
                if had_data:
                    stocks_with_data += 1
                all_trades.extend(trades)
            except Exception as exc:
                logger.debug("回測失敗 %s: %s", futures[future], exc)

    signal_count = len({(t.stock_code, t.signal_date) for t in all_trades if t.valid})
    summary = aggregate_trades(all_trades, source="historical")
    for ps in summary.period_stats:
        ps.label = STRATEGY_LABEL
    summary.stocks_scanned = len(codes)
    summary.stocks_with_data = stocks_with_data
    summary.signal_count = signal_count
    save_summary(summary, period_tag=period_tag)
    save_trades_csv(all_trades, period_tag=period_tag)
    logger.info(
        "長下影線反轉回測完成：%d 檔有資料 / %d 檔，%d 信號，%d 筆交易",
        stocks_with_data,
        len(codes),
        signal_count,
        len(all_trades),
    )
    return summary


def get_or_run_shadow_reversal_backtest(
    refresh: bool = False,
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    history_years: int = HISTORY_YEARS,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    period_tag: Optional[str] = None,
) -> BacktestSummary:
    start, end = resolve_backtest_window(history_years, start_date, end_date)
    explicit_window = start_date is not None or end_date is not None
    if period_tag is not None:
        tag = period_tag
    elif explicit_window:
        tag = f"{start.year}" if start.year == end.year else f"{start.year}_{end.year}"
    else:
        tag = None

    if not refresh and _cache_fresh(output_paths(tag)[0]):
        cached = load_cached_summary(tag)
        if cached is not None:
            logger.info("使用長下影線反轉回測快取")
            return cached

    return run_shadow_reversal_backtest(
        max_workers=max_workers,
        stock_limit=stock_limit,
        history_years=history_years,
        start_date=start_date,
        end_date=end_date,
        period_tag=period_tag,
    )
