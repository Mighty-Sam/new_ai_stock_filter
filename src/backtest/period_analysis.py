"""年度對照與月度勝率分析。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.backtest.sl_tp_backtest import output_paths as v1_output_paths
from src.backtest.strategy_grid import grid_output_paths as v2_output_paths
from src.data.price_fetcher import PriceFetcher

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")


@dataclass(frozen=True)
class ComboPreset:
    label: str
    strategy: str  # v1 | v2
    stop_type: str
    tp_type: str
    min_oscillation: Optional[int] = None
    max_hold_days: Optional[int] = None
    entry_mode: Optional[str] = None


PRESET_COMBOS: Dict[str, ComboPreset] = {
    "v1_bull": ComboPreset(
        label="v1 牛市參數 (-10% / +30%)",
        strategy="v1",
        stop_type="pct_10",
        tp_type="pct_30",
    ),
    "v1_bear": ComboPreset(
        label="v1 熊市參數 (上穿均價 / +10%)",
        strategy="v1",
        stop_type="cross_ma",
        tp_type="pct_10",
    ),
    "v2_bull": ComboPreset(
        label="v2 近1年最佳 (-10% / +30% / 30日 / 信號收盤)",
        strategy="v2",
        stop_type="pct_10",
        tp_type="pct_30",
        min_oscillation=3,
        max_hold_days=30,
        entry_mode="signal_close",
    ),
}


@dataclass
class MonthStats:
    month: str
    trade_count: int
    signal_count: int
    win_rate: float
    avg_return_pct: float
    median_return_pct: float


@dataclass
class YearComboReport:
    preset: str
    label: str
    year: int
    trade_count: int
    signal_count: int
    win_rate: float
    avg_return_pct: float
    median_return_pct: float
    profit_factor: Optional[float]
    benchmark_return_pct: Optional[float] = None
    alpha_vs_benchmark_pct: Optional[float] = None
    monthly: List[MonthStats] = field(default_factory=list)


def _trades_path(strategy: str, year: int) -> Path:
    tag = str(year)
    if strategy == "v1":
        _, path = v1_output_paths(tag)
    else:
        _, path = v2_output_paths(tag)
    return path


def _summary_path(strategy: str, year: int) -> Path:
    tag = str(year)
    if strategy == "v1":
        path, _ = v1_output_paths(tag)
    else:
        path, _ = v2_output_paths(tag)
    return path


def _profit_factor(returns: pd.Series) -> Optional[float]:
    wins = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    if losses == 0:
        return float("inf") if wins > 0 else None
    return round(wins / losses, 2)


def benchmark_return(year: int) -> Optional[float]:
    fetcher = PriceFetcher(delay=0)
    df = fetcher.fetch("0050", days=800, end_date=date(year, 12, 31), min_rows=200)
    if df is None or df.empty:
        return None
    sub = df.sort_index().loc[f"{year}-01-01":f"{year}-12-31"]
    if len(sub) < 2:
        return None
    return round((float(sub["close"].iloc[-1]) / float(sub["close"].iloc[0]) - 1) * 100, 2)


def load_trades(strategy: str, year: int) -> pd.DataFrame:
    path = _trades_path(strategy, year)
    if not path.exists():
        fallback = DATA_DIR / (
            "sl_tp_backtest_trades.csv" if strategy == "v1" else "strategy_grid_trades.csv"
        )
        if fallback.exists() and _summary_path(strategy, year).exists():
            logger.info("使用 %s 作為 %d 年 %s 交易明細", fallback.name, year, strategy)
            path = fallback
        else:
            raise FileNotFoundError(f"找不到 {path}，請先執行 {year} 年回測")
    df = pd.read_csv(path)
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    start = pd.Timestamp(f"{year}-01-01")
    end = pd.Timestamp(f"{year}-12-31")
    mask = (df["signal_date"] >= start) & (df["signal_date"] <= end)
    return df.loc[mask].copy()


def filter_combo(df: pd.DataFrame, preset: ComboPreset) -> pd.DataFrame:
    sub = df[(df["stop_type"] == preset.stop_type) & (df["tp_type"] == preset.tp_type)]
    if preset.min_oscillation is not None:
        sub = sub[sub["min_oscillation"] == preset.min_oscillation]
    if preset.max_hold_days is not None:
        sub = sub[sub["max_hold_days"] == preset.max_hold_days]
    if preset.entry_mode is not None:
        sub = sub[sub["entry_mode"] == preset.entry_mode]
    return sub


def monthly_breakdown(df: pd.DataFrame) -> List[MonthStats]:
    if df.empty:
        return []

    work = df.copy()
    work["month"] = work["signal_date"].dt.to_period("M").astype(str)
    rows: List[MonthStats] = []
    for month, grp in work.groupby("month", sort=True):
        signals = grp.groupby(["stock_code", "signal_date"]).ngroups
        rows.append(
            MonthStats(
                month=str(month),
                trade_count=len(grp),
                signal_count=int(signals),
                win_rate=round(grp["is_win"].mean() * 100, 1),
                avg_return_pct=round(grp["return_pct"].mean(), 2),
                median_return_pct=round(grp["return_pct"].median(), 2),
            )
        )
    return rows


def year_combo_report(preset_key: str, year: int) -> YearComboReport:
    preset = PRESET_COMBOS[preset_key]
    df = load_trades(preset.strategy, year)
    sub = filter_combo(df, preset)
    bench = benchmark_return(year)
    avg = round(sub["return_pct"].mean(), 2) if len(sub) else 0.0
    signals = sub.groupby(["stock_code", "signal_date"]).ngroups if len(sub) else 0
    return YearComboReport(
        preset=preset_key,
        label=preset.label,
        year=year,
        trade_count=len(sub),
        signal_count=int(signals),
        win_rate=round(sub["is_win"].mean() * 100, 1) if len(sub) else 0.0,
        avg_return_pct=avg,
        median_return_pct=round(sub["return_pct"].median(), 2) if len(sub) else 0.0,
        profit_factor=_profit_factor(sub["return_pct"]) if len(sub) else None,
        benchmark_return_pct=bench,
        alpha_vs_benchmark_pct=round(avg - bench, 2) if bench is not None and len(sub) else None,
        monthly=monthly_breakdown(sub),
    )


def ensure_backtest_data(
    years: List[int],
    refresh: bool = False,
    max_workers: int = 8,
) -> None:
    from src.backtest.sl_tp_backtest import get_or_run_sl_tp_backtest
    from src.backtest.strategy_grid import get_or_run_strategy_grid

    start_end = [(date(y, 1, 1), date(y, 12, 31)) for y in years]
    for y, (start, end) in zip(years, start_end):
        for strategy, runner in (
            ("v1", lambda s=start, e=end: get_or_run_sl_tp_backtest(
                refresh=refresh,
                max_workers=max_workers,
                start_date=s,
                end_date=e,
            )),
            ("v2", lambda s=start, e=end: get_or_run_strategy_grid(
                refresh=refresh,
                max_workers=max_workers,
                start_date=s,
                end_date=e,
            )),
        ):
            trades_path = _trades_path(strategy, y)
            summary_path = _summary_path(strategy, y)
            if refresh or (not trades_path.exists() and not summary_path.exists()):
                logger.info("執行 %s %d 年回測…", strategy, y)
                runner()
            elif not trades_path.exists() and summary_path.exists():
                logger.info("缺少 %s，重跑 %s %d 年回測…", trades_path.name, strategy, y)
                runner()


def build_period_report(
    years: List[int],
    presets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    presets = presets or list(PRESET_COMBOS.keys())
    benchmarks = {y: benchmark_return(y) for y in years}
    combo_reports: List[Dict[str, Any]] = []
    for preset_key in presets:
        for year in years:
            try:
                report = year_combo_report(preset_key, year)
                combo_reports.append(asdict(report))
            except FileNotFoundError as exc:
                combo_reports.append(
                    {"preset": preset_key, "year": year, "error": str(exc)}
                )

    return {
        "years": years,
        "benchmarks": benchmarks,
        "presets": {k: asdict(v) for k, v in PRESET_COMBOS.items() if k in presets},
        "combo_reports": combo_reports,
        "updated_at": pd.Timestamp.now().isoformat(),
    }


def save_period_report(report: Dict[str, Any], years: List[int]) -> Path:
    tag = "_".join(str(y) for y in years)
    path = DATA_DIR / f"period_analysis_{tag}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
