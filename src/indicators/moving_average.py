"""移動平均線計算。"""

from __future__ import annotations

import pandas as pd

MA_PERIODS = (5, 10, 20, 60, 120)


def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """在 DataFrame 上新增 MA5/10/20/60/120 欄位。"""
    result = df.copy()
    close = result["close"]
    for period in MA_PERIODS:
        result[f"ma{period}"] = close.rolling(window=period, min_periods=period).mean()
    return result
