"""v2 策略參數網格回測。"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import product
from pathlib import Path
from typing import List, Optional

import pandas as pd
from tqdm import tqdm

from src.backtest.sl_tp_backtest import data_fetch_end_date, resolve_backtest_window
from src.backtest.sl_tp_simulator_v2 import (
    ENTRY_LABELS,
    STOP_LABELS,
    TP_LABELS,
    EntryMode,
    SlTpConfig,
    SlTpTradeResultV2,
    StopLossType,
    TakeProfitType,
    simulate_sl_tp_v2,
)
from src.data.price_fetcher import PriceFetcher
from src.data.stock_list import get_stock_list
from src.indicators.moving_average import add_moving_averages
from src.screener.conditions import ScreenResult, evaluate_as_of
from src.screener.params import V2_BASE_PARAMS

logger = logging.getLogger(__name__)

HISTORY_YEARS = 1
SUMMARY_CACHE_PATH = Path("data/strategy_grid_summary.json")
TRADES_CSV_PATH = Path("data/strategy_grid_trades.csv")
V1_VS_V2_PATH = Path("data/v1_vs_v2_baseline.json")
CACHE_TTL_HOURS = 24
MIN_WARMUP = 120
MAX_HOLD = 30


def grid_output_paths(period_tag: Optional[str] = None) -> tuple[Path, Path]:
    if period_tag:
        return (
            Path(f"data/strategy_grid_summary_{period_tag}.json"),
            Path(f"data/strategy_grid_trades_{period_tag}.csv"),
        )
    return SUMMARY_CACHE_PATH, TRADES_CSV_PATH

GRID_STOP_TYPES: tuple[StopLossType, ...] = ("pct_10", "cross_low", "cross_skip_day1")
GRID_TP_TYPES: tuple[TakeProfitType, ...] = ("pct_25", "pct_30")
GRID_HOLDS: tuple[int, ...] = (20, 30)
GRID_MIN_OSCILLATIONS: tuple[int, ...] = (3, 5, 6)
GRID_ENTRY_MODES: tuple[EntryMode, ...] = ("next_open", "signal_close")


@dataclass
class GridComboStats:
    min_oscillation: int
    stop_type: str
    tp_type: str
    max_hold_days: int
    entry_mode: str
    sample_count: int = 0
    signal_count: int = 0
    win_rate: float = 0.0
    avg_return_pct: float = 0.0
    median_return_pct: float = 0.0
    profit_factor: Optional[float] = None
    avg_win_loss_ratio: Optional[float] = None
    stop_rate: float = 0.0
    tp_rate: float = 0.0
    timeout_rate: float = 0.0


@dataclass
class StrategyGridSummary:
    combo_stats: List[GridComboStats] = field(default_factory=list)
    updated_at: Optional[str] = None
    from_cache: bool = False
    stocks_scanned: int = 0
    stocks_with_data: int = 0
    total_signals_v2: int = 0
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


def grid_combo_key(
    min_osc: int,
    stop: str,
    tp: str,
    hold: int,
    entry: str,
) -> tuple:
    return (min_osc, stop, tp, hold, entry)


def aggregate_grid_stats(trades: List[SlTpTradeResultV2]) -> List[GridComboStats]:
    if not trades:
        return []

    rows = [
        {
            "min_oscillation": t.min_oscillation,
            "stop_type": t.stop_type,
            "tp_type": t.tp_type,
            "max_hold_days": t.max_hold_days,
            "entry_mode": t.entry_mode,
            "return_pct": t.return_pct,
            "exit_reason": t.exit_reason,
            "stock_code": t.stock_code,
            "signal_date": t.signal_date,
        }
        for t in trades
        if t.valid
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return []

    stats: List[GridComboStats] = []
    for min_osc in GRID_MIN_OSCILLATIONS:
        for stop, tp, hold, entry in product(
            GRID_STOP_TYPES, GRID_TP_TYPES, GRID_HOLDS, GRID_ENTRY_MODES
        ):
            sub = df[
                (df["min_oscillation"] == min_osc)
                & (df["stop_type"] == stop)
                & (df["tp_type"] == tp)
                & (df["max_hold_days"] == hold)
                & (df["entry_mode"] == entry)
            ]
            n = len(sub)
            if n == 0:
                continue
            sig_n = sub.groupby(["stock_code", "signal_date"]).ngroups
            stats.append(
                GridComboStats(
                    min_oscillation=min_osc,
                    stop_type=stop,
                    tp_type=tp,
                    max_hold_days=hold,
                    entry_mode=entry,
                    sample_count=n,
                    signal_count=sig_n,
                    win_rate=round((sub["return_pct"] > 0).mean() * 100, 1),
                    avg_return_pct=round(sub["return_pct"].mean(), 2),
                    median_return_pct=round(sub["return_pct"].median(), 2),
                    profit_factor=_profit_factor(sub["return_pct"]),
                    avg_win_loss_ratio=_avg_win_loss_ratio(sub["return_pct"]),
                    stop_rate=round((sub["exit_reason"] == "stop").mean() * 100, 1),
                    tp_rate=round((sub["exit_reason"] == "take_profit").mean() * 100, 1),
                    timeout_rate=round((sub["exit_reason"] == "timeout").mean() * 100, 1),
                )
            )
    return stats


def _tagged_trade(trade: SlTpTradeResultV2, min_osc: int) -> SlTpTradeResultV2:
    trade.min_oscillation = min_osc
    return trade


def _simulate_signal_grid(
    stock_code: str,
    df: pd.DataFrame,
    signal: ScreenResult,
) -> List[SlTpTradeResultV2]:
    trades: List[SlTpTradeResultV2] = []
    osc = signal.oscillation_bars
    for min_osc in GRID_MIN_OSCILLATIONS:
        if osc < min_osc:
            continue
        for stop, tp, hold, entry in product(
            GRID_STOP_TYPES, GRID_TP_TYPES, GRID_HOLDS, GRID_ENTRY_MODES
        ):
            cfg = SlTpConfig(
                stop_type=stop,
                tp_type=tp,
                max_hold_days=hold,
                entry_mode=entry,
            )
            trade = simulate_sl_tp_v2(stock_code, df, signal, cfg)
            if trade is not None:
                trades.append(_tagged_trade(trade, min_osc))
    return trades


def _backtest_single_stock(
    stock_code: str,
    start_date: date,
    end_date: date,
) -> tuple[List[SlTpTradeResultV2], bool]:
    fetcher = PriceFetcher(delay=0.05)
    fetch_end = data_fetch_end_date(end_date, forward_buffer_days=60)
    span = max((fetch_end - start_date).days, (end_date - start_date).days)
    days = span + MIN_WARMUP + MAX_HOLD + 60
    df = fetcher.fetch(
        stock_code,
        days=days,
        end_date=fetch_end,
        min_rows=MIN_WARMUP + MAX_HOLD + 1,
    )
    if df is None or len(df) < MIN_WARMUP + MAX_HOLD + 1:
        return [], False

    df = add_moving_averages(df)
    trades: List[SlTpTradeResultV2] = []

    for i in range(MIN_WARMUP, len(df) - MAX_HOLD):
        as_of = df.index[i]
        if as_of.date() < start_date:
            continue
        if as_of.date() > end_date:
            break

        result = evaluate_as_of(
            df, as_of, stock_code=stock_code, params=V2_BASE_PARAMS
        )
        if result is None:
            continue

        trades.extend(_simulate_signal_grid(stock_code, df, result))

    return trades, True


def _stats_to_dict(cs: GridComboStats) -> dict:
    pf = cs.profit_factor
    if pf == float("inf"):
        pf = "inf"
    return {
        "min_oscillation": cs.min_oscillation,
        "stop_type": cs.stop_type,
        "tp_type": cs.tp_type,
        "max_hold_days": cs.max_hold_days,
        "entry_mode": cs.entry_mode,
        "sample_count": cs.sample_count,
        "signal_count": cs.signal_count,
        "win_rate": cs.win_rate,
        "avg_return_pct": cs.avg_return_pct,
        "median_return_pct": cs.median_return_pct,
        "profit_factor": pf,
        "avg_win_loss_ratio": cs.avg_win_loss_ratio,
        "stop_rate": cs.stop_rate,
        "tp_rate": cs.tp_rate,
        "timeout_rate": cs.timeout_rate,
    }


def _summary_to_dict(summary: StrategyGridSummary) -> dict:
    return {
        "updated_at": summary.updated_at,
        "stocks_scanned": summary.stocks_scanned,
        "stocks_with_data": summary.stocks_with_data,
        "total_signals_v2": summary.total_signals_v2,
        "trade_count": summary.trade_count,
        "history_years": summary.history_years,
        "start_date": summary.start_date,
        "end_date": summary.end_date,
        "grid_size": len(GRID_STOP_TYPES)
        * len(GRID_TP_TYPES)
        * len(GRID_HOLDS)
        * len(GRID_MIN_OSCILLATIONS)
        * len(GRID_ENTRY_MODES),
        "combo_stats": [_stats_to_dict(cs) for cs in summary.combo_stats],
    }


def save_summary(summary: StrategyGridSummary, period_tag: Optional[str] = None) -> None:
    path, _ = grid_output_paths(period_tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(_summary_to_dict(summary), f, ensure_ascii=False, indent=2)


def save_trades_csv(trades: List[SlTpTradeResultV2], period_tag: Optional[str] = None) -> None:
    if not trades:
        return
    _, path = grid_output_paths(period_tag)
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
            "min_oscillation": t.min_oscillation,
            "stop_type": t.stop_type,
            "tp_type": t.tp_type,
            "max_hold_days": t.max_hold_days,
            "entry_mode": t.entry_mode,
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


def run_strategy_grid(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    history_years: int = HISTORY_YEARS,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    period_tag: Optional[str] = None,
) -> StrategyGridSummary:
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

    all_trades: List[SlTpTradeResultV2] = []
    stocks_with_data = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_backtest_single_stock, code, start_date, end_date): code
            for code in codes
        }
        for future in tqdm(as_completed(futures), total=len(futures), desc="策略網格"):
            try:
                trades, had_data = future.result()
                if had_data:
                    stocks_with_data += 1
                all_trades.extend(trades)
            except Exception as exc:
                logger.debug("策略網格失敗 %s: %s", futures[future], exc)

    signal_keys = {(t.stock_code, t.signal_date) for t in all_trades if t.valid}
    combo_stats = aggregate_grid_stats(all_trades)
    summary = StrategyGridSummary(
        combo_stats=combo_stats,
        updated_at=pd.Timestamp.now().isoformat(),
        from_cache=False,
        stocks_scanned=len(codes),
        stocks_with_data=stocks_with_data,
        total_signals_v2=len(signal_keys),
        trade_count=len(all_trades),
        history_years=history_years,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    save_summary(summary, period_tag=period_tag)
    save_trades_csv(all_trades, period_tag=period_tag)
    logger.info(
        "策略網格完成：%d 檔有資料，%d 信號，%d 筆交易，%d 組合",
        stocks_with_data,
        len(signal_keys),
        len(all_trades),
        len(combo_stats),
    )
    return summary


def get_or_run_strategy_grid(
    refresh: bool = False,
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    history_years: int = HISTORY_YEARS,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    period_tag: Optional[str] = None,
) -> StrategyGridSummary:
    start, end = resolve_backtest_window(history_years, start_date, end_date)
    tag = period_tag or (f"{start.year}" if start_date or end_date else None)
    summary_path, _ = grid_output_paths(tag)

    if not refresh and _cache_fresh(summary_path):
        try:
            raw = json.loads(summary_path.read_text(encoding="utf-8"))
            if raw.get("combo_stats"):
                stats = [
                    GridComboStats(
                        **{
                            k: (float("inf") if v == "inf" else v)
                            for k, v in cs.items()
                            if k in GridComboStats.__dataclass_fields__
                        }
                    )
                    for cs in raw["combo_stats"]
                ]
                return StrategyGridSummary(
                    combo_stats=stats,
                    updated_at=raw.get("updated_at"),
                    from_cache=True,
                    stocks_scanned=raw.get("stocks_scanned", 0),
                    stocks_with_data=raw.get("stocks_with_data", 0),
                    total_signals_v2=raw.get("total_signals_v2", 0),
                    trade_count=raw.get("trade_count", 0),
                    history_years=raw.get("history_years", history_years),
                    start_date=raw.get("start_date"),
                    end_date=raw.get("end_date"),
                )
        except Exception as exc:
            logger.warning("讀取策略網格快取失敗: %s", exc)

    return run_strategy_grid(
        max_workers=max_workers,
        stock_limit=stock_limit,
        history_years=history_years,
        start_date=start_date,
        end_date=end_date,
        period_tag=tag,
    )
