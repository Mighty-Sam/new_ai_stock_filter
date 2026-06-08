"""近 3 年止損/止盈組合回測。"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd
from tqdm import tqdm

from src.backtest.sl_tp_simulator import (
    STOP_LOSS_TYPES,
    TAKE_PROFIT_TYPES,
    SlTpTradeResult,
    simulate_all_combos,
)
from src.data.price_fetcher import PriceFetcher
from src.data.stock_list import get_stock_list
from src.indicators.moving_average import add_moving_averages
from src.screener.conditions import ScreenResult, evaluate_as_of

logger = logging.getLogger(__name__)

HISTORY_YEARS = 3
SUMMARY_CACHE_PATH = Path("data/sl_tp_backtest_summary.json")
TRADES_CSV_PATH = Path("data/sl_tp_backtest_trades.csv")
CACHE_TTL_HOURS = 24
MIN_WARMUP = 120
MIN_FORWARD = 20


def resolve_backtest_window(
    history_years: int = HISTORY_YEARS,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> tuple[date, date]:
    """信號篩選區間；未指定 end 則為今日，未指定 start 則往前 history_years。"""
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=365 * history_years))
    return start, end


def data_fetch_end_date(signal_end: date, forward_buffer_days: int = 60) -> date:
    """抓取股價需延伸至信號區間後，供持有期模擬。"""
    return min(signal_end + timedelta(days=forward_buffer_days), date.today())


def output_paths(period_tag: Optional[str] = None) -> tuple[Path, Path]:
    if period_tag:
        return (
            Path(f"data/sl_tp_backtest_summary_{period_tag}.json"),
            Path(f"data/sl_tp_backtest_trades_{period_tag}.csv"),
        )
    return SUMMARY_CACHE_PATH, TRADES_CSV_PATH


@dataclass
class ComboStats:
    stop_type: str
    tp_type: str
    sample_count: int = 0
    win_rate: float = 0.0
    avg_return_pct: float = 0.0
    median_return_pct: float = 0.0
    profit_factor: Optional[float] = None
    avg_win_loss_ratio: Optional[float] = None
    stop_rate: float = 0.0
    tp_rate: float = 0.0
    timeout_rate: float = 0.0


@dataclass
class SlTpBacktestSummary:
    combo_stats: List[ComboStats] = field(default_factory=list)
    updated_at: Optional[str] = None
    from_cache: bool = False
    stocks_scanned: int = 0
    stocks_with_data: int = 0
    signal_count: int = 0
    trade_count: int = 0
    history_years: int = HISTORY_YEARS
    start_date: Optional[str] = None
    end_date: Optional[str] = None


def _cache_fresh(path: Path, ttl_hours: int = CACHE_TTL_HOURS) -> bool:
    if not path.exists():
        return False
    age = pd.Timestamp.now() - pd.Timestamp(path.stat().st_mtime, unit="s")
    return age.total_seconds() < ttl_hours * 3600


def _profit_factor(returns: pd.Series) -> Optional[float]:
    wins = returns[returns > 0].sum()
    losses = returns[returns < 0].sum()
    if losses == 0:
        return None if wins == 0 else float("inf")
    return round(wins / abs(losses), 2)


def _avg_win_loss_ratio(returns: pd.Series) -> Optional[float]:
    win_mean = returns[returns > 0].mean()
    loss_mean = returns[returns < 0].mean()
    if pd.isna(win_mean) or pd.isna(loss_mean) or loss_mean == 0:
        return None
    return round(win_mean / abs(loss_mean), 2)


def aggregate_sl_tp_stats(trades: List[SlTpTradeResult]) -> List[ComboStats]:
    if not trades:
        return []

    df = pd.DataFrame(
        [
            {
                "stop_type": t.stop_type,
                "tp_type": t.tp_type,
                "return_pct": t.return_pct,
                "exit_reason": t.exit_reason,
                "valid": t.valid,
            }
            for t in trades
            if t.valid
        ]
    )
    if df.empty:
        return []

    stats: List[ComboStats] = []
    for stop_type in STOP_LOSS_TYPES:
        for tp_type in TAKE_PROFIT_TYPES:
            sub = df[(df["stop_type"] == stop_type) & (df["tp_type"] == tp_type)]
            n = len(sub)
            if n == 0:
                continue

            pf = _profit_factor(sub["return_pct"])
            wl = _avg_win_loss_ratio(sub["return_pct"])
            stats.append(
                ComboStats(
                    stop_type=stop_type,
                    tp_type=tp_type,
                    sample_count=n,
                    win_rate=round((sub["return_pct"] > 0).mean() * 100, 1),
                    avg_return_pct=round(sub["return_pct"].mean(), 2),
                    median_return_pct=round(sub["return_pct"].median(), 2),
                    profit_factor=pf,
                    avg_win_loss_ratio=wl,
                    stop_rate=round((sub["exit_reason"] == "stop").mean() * 100, 1),
                    tp_rate=round((sub["exit_reason"] == "take_profit").mean() * 100, 1),
                    timeout_rate=round((sub["exit_reason"] == "timeout").mean() * 100, 1),
                )
            )
    return stats


def _summary_from_dict(data: dict) -> SlTpBacktestSummary:
    combo_stats = [
        ComboStats(
            stop_type=cs["stop_type"],
            tp_type=cs["tp_type"],
            sample_count=cs["sample_count"],
            win_rate=cs["win_rate"],
            avg_return_pct=cs["avg_return_pct"],
            median_return_pct=cs["median_return_pct"],
            profit_factor=cs.get("profit_factor"),
            avg_win_loss_ratio=cs.get("avg_win_loss_ratio"),
            stop_rate=cs["stop_rate"],
            tp_rate=cs["tp_rate"],
            timeout_rate=cs["timeout_rate"],
        )
        for cs in data.get("combo_stats", [])
    ]
    return SlTpBacktestSummary(
        combo_stats=combo_stats,
        updated_at=data.get("updated_at"),
        from_cache=True,
        stocks_scanned=data.get("stocks_scanned", 0),
        stocks_with_data=data.get("stocks_with_data", 0),
        signal_count=data.get("signal_count", 0),
        trade_count=data.get("trade_count", 0),
        history_years=data.get("history_years", HISTORY_YEARS),
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
    )


def _summary_to_dict(summary: SlTpBacktestSummary) -> dict:
    def _pf_value(pf: Optional[float]) -> Optional[float | str]:
        if pf is None:
            return None
        if pf == float("inf"):
            return "inf"
        return pf

    return {
        "updated_at": summary.updated_at,
        "stocks_scanned": summary.stocks_scanned,
        "stocks_with_data": summary.stocks_with_data,
        "signal_count": summary.signal_count,
        "trade_count": summary.trade_count,
        "history_years": summary.history_years,
        "start_date": summary.start_date,
        "end_date": summary.end_date,
        "combo_stats": [
            {
                "stop_type": cs.stop_type,
                "tp_type": cs.tp_type,
                "sample_count": cs.sample_count,
                "win_rate": cs.win_rate,
                "avg_return_pct": cs.avg_return_pct,
                "median_return_pct": cs.median_return_pct,
                "profit_factor": _pf_value(cs.profit_factor),
                "avg_win_loss_ratio": cs.avg_win_loss_ratio,
                "stop_rate": cs.stop_rate,
                "tp_rate": cs.tp_rate,
                "timeout_rate": cs.timeout_rate,
            }
            for cs in summary.combo_stats
        ],
    }


def load_cached_summary() -> Optional[SlTpBacktestSummary]:
    if not SUMMARY_CACHE_PATH.exists():
        return None
    try:
        with SUMMARY_CACHE_PATH.open(encoding="utf-8") as f:
            raw = json.load(f)
        summary = _summary_from_dict(raw)
        for cs in summary.combo_stats:
            pf = next(
                (
                    row.get("profit_factor")
                    for row in raw.get("combo_stats", [])
                    if row["stop_type"] == cs.stop_type and row["tp_type"] == cs.tp_type
                ),
                None,
            )
            if pf == "inf":
                cs.profit_factor = float("inf")
        return summary
    except Exception as exc:
        logger.warning("讀取 SL/TP 回測快取失敗: %s", exc)
        return None


def save_summary(summary: SlTpBacktestSummary, period_tag: Optional[str] = None) -> None:
    path, _ = output_paths(period_tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(_summary_to_dict(summary), f, ensure_ascii=False, indent=2)


def save_trades_csv(trades: List[SlTpTradeResult], period_tag: Optional[str] = None) -> None:
    if not trades:
        return
    _, path = output_paths(period_tag)
    rows = [
        {
            "stock_code": t.stock_code,
            "signal_date": t.signal_date.isoformat(),
            "entry_date": t.entry_date.isoformat(),
            "entry_price": t.entry_price,
            "exit_date": t.exit_date.isoformat(),
            "exit_price": t.exit_price,
            "hold_days": t.hold_days,
            "return_pct": t.return_pct,
            "stop_type": t.stop_type,
            "tp_type": t.tp_type,
            "exit_reason": t.exit_reason,
            "stop_price": t.stop_price,
            "tp_price": t.tp_price,
            "cross_ref_price": t.cross_ref_price,
            "is_win": t.is_win,
        }
        for t in trades
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _backtest_single_stock(
    stock_code: str,
    start_date: date,
    end_date: date,
) -> tuple[List[SlTpTradeResult], bool]:
    fetcher = PriceFetcher(delay=0.05)
    fetch_end = data_fetch_end_date(end_date, forward_buffer_days=60)
    span = max((fetch_end - start_date).days, (end_date - start_date).days)
    days = span + MIN_WARMUP + MIN_FORWARD + 60
    df = fetcher.fetch(
        stock_code,
        days=days,
        end_date=fetch_end,
        min_rows=MIN_WARMUP + MIN_FORWARD + 1,
    )
    if df is None or len(df) < MIN_WARMUP + MIN_FORWARD + 1:
        return [], False

    df = add_moving_averages(df)
    trades: List[SlTpTradeResult] = []

    for i in range(MIN_WARMUP, len(df) - MIN_FORWARD):
        as_of = df.index[i]
        if as_of.date() < start_date:
            continue
        if as_of.date() > end_date:
            break

        result = evaluate_as_of(df, as_of, stock_code=stock_code)
        if result is None:
            continue

        trades.extend(simulate_all_combos(stock_code, df, result))

    return trades, True


def run_sl_tp_backtest(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    history_years: int = HISTORY_YEARS,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    period_tag: Optional[str] = None,
) -> SlTpBacktestSummary:
    explicit_window = start_date is not None or end_date is not None
    start_date, end_date = resolve_backtest_window(
        history_years=history_years,
        start_date=start_date,
        end_date=end_date,
    )
    if period_tag is None and explicit_window and start_date.year == end_date.year:
        period_tag = f"{start_date.year}"

    stocks = get_stock_list()
    codes = sorted(stocks.keys())
    if stock_limit:
        codes = codes[:stock_limit]

    all_trades: List[SlTpTradeResult] = []
    stocks_with_data = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_backtest_single_stock, code, start_date, end_date): code
            for code in codes
        }
        for future in tqdm(as_completed(futures), total=len(futures), desc="SL/TP 回測"):
            try:
                trades, had_data = future.result()
                if had_data:
                    stocks_with_data += 1
                all_trades.extend(trades)
            except Exception as exc:
                logger.debug("SL/TP 回測失敗 %s: %s", futures[future], exc)

    signal_count = len({(t.stock_code, t.signal_date) for t in all_trades if t.valid})
    combo_stats = aggregate_sl_tp_stats(all_trades)
    summary = SlTpBacktestSummary(
        combo_stats=combo_stats,
        updated_at=pd.Timestamp.now().isoformat(),
        from_cache=False,
        stocks_scanned=len(codes),
        stocks_with_data=stocks_with_data,
        signal_count=signal_count,
        trade_count=len(all_trades),
        history_years=history_years,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    save_summary(summary, period_tag=period_tag)
    save_trades_csv(all_trades, period_tag=period_tag)
    logger.info(
        "SL/TP 回測完成：%d 檔有資料 / %d 檔，%d 信號，%d 筆交易",
        stocks_with_data,
        len(codes),
        signal_count,
        len(all_trades),
    )
    return summary


def get_or_run_sl_tp_backtest(
    refresh: bool = False,
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    history_years: int = HISTORY_YEARS,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    period_tag: Optional[str] = None,
) -> SlTpBacktestSummary:
    start, end = resolve_backtest_window(history_years, start_date, end_date)
    tag = period_tag or (f"{start.year}" if start_date or end_date else None)
    summary_path, _ = output_paths(tag)

    if not refresh and _cache_fresh(summary_path):
        if summary_path != SUMMARY_CACHE_PATH:
            try:
                with summary_path.open(encoding="utf-8") as f:
                    raw = json.load(f)
                return _summary_from_dict(raw)
            except Exception:
                pass
        else:
            cached = load_cached_summary()
            if cached and cached.combo_stats and cached.history_years == history_years:
                logger.info("使用 SL/TP 回測快取")
                return cached

    return run_sl_tp_backtest(
        max_workers=max_workers,
        stock_limit=stock_limit,
        history_years=history_years,
        start_date=start_date,
        end_date=end_date,
        period_tag=tag,
    )
