"""台股交易日曆（以 0050 基準 K 棒為準）。"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

import pandas as pd


def get_trading_days(benchmark_df: pd.DataFrame) -> List[date]:
    """由 0050 DatetimeIndex 取得排序後的交易日列表。"""
    if benchmark_df is None or benchmark_df.empty:
        return []
    return sorted(pd.Timestamp(ts).date() for ts in benchmark_df.sort_index().index)


def offset_trading_days(
    ref: date,
    n: int,
    benchmark_df: pd.DataFrame,
) -> Optional[date]:
    """
    以 ref 為基準偏移 n 個交易日。
    n=-20 表示往回 20 個交易日；n=0 表示 ref 當日或之前最近一個交易日。
    """
    days = get_trading_days(benchmark_df)
    if not days:
        return None

    ref_ts = pd.Timestamp(ref)
    idx = None
    for i, d in enumerate(days):
        if pd.Timestamp(d) <= ref_ts:
            idx = i
        else:
            break

    if idx is None:
        return None

    target_idx = idx + n
    if target_idx < 0 or target_idx >= len(days):
        return None
    return days[target_idx]
