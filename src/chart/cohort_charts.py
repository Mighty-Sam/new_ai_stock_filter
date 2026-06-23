"""批次回測 K 線圖產生。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from src.backtest.tracker import MaturityCohortReport
from src.chart.candlestick import plot_backtest_candlestick
from src.data.price_fetcher import PriceFetcher
from src.indicators.moving_average import add_moving_averages

logger = logging.getLogger(__name__)


def build_cohort_charts(
    cohort: MaturityCohortReport,
    stock_names: Dict[str, str],
    output_dir: Path,
    fetcher: Optional[PriceFetcher] = None,
) -> Dict[str, Path]:
    """為批次回測每檔產生含進出場標註的 K 線圖。"""
    if not cohort.has_trades or cohort.signal_date is None:
        return {}

    output_dir.mkdir(parents=True, exist_ok=True)
    price_fetcher = fetcher or PriceFetcher(delay=0.05)
    chart_paths: Dict[str, Path] = {}

    for trade in cohort.trades:
        df = price_fetcher.fetch(
            trade.stock_code,
            days=120,
            end_date=cohort.scan_date,
        )
        if df is None or df.empty:
            logger.warning("回測圖表：%s 無價格資料", trade.stock_code)
            continue

        df = add_moving_averages(df)
        name = stock_names.get(trade.stock_code, "")
        path = plot_backtest_candlestick(
            df=df,
            stock_code=trade.stock_code,
            stock_name=name,
            signal_date=trade.signal_date,
            entry_date=trade.entry_date,
            entry_price=trade.entry_price,
            exit_date=trade.exit_date,
            exit_price=trade.exit_price,
            exit_reason=trade.exit_reason,
            output_path=output_dir / f"{trade.stock_code}.png",
        )
        chart_paths[trade.stock_code] = path
        logger.info("回測圖表：%s → %s", trade.stock_code, path)

    return chart_paths
