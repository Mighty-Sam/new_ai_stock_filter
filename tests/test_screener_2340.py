"""台亞 2340 @ 2021/11/16 驗證測試。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.data.price_fetcher import PriceFetcher
from src.indicators.moving_average import add_moving_averages
from src.screener.conditions import evaluate_as_of


SIGNAL_DATE = pd.Timestamp("2021-11-16")
STOCK_CODE = "2340"


@pytest.fixture(scope="module")
def taiya_df():
    fetcher = PriceFetcher(delay=0.2)
    df = fetcher.fetch_as_of(STOCK_CODE, as_of=date(2021, 11, 16), days=250)
    if df is None or df.empty:
        pytest.skip("無法取得 2340 歷史資料（需網路）")
    return add_moving_averages(df)


def test_taiya_20211116_passes_screen(taiya_df):
    result = evaluate_as_of(
        taiya_df,
        as_of=SIGNAL_DATE,
        stock_code=STOCK_CODE,
        min_volume=500_000,
    )
    assert result is not None, "台亞 2340 在 2021/11/16 應符合選股條件"
    assert result.stock_code == STOCK_CODE
    assert result.gain_pct > 10
    assert result.retest_ma in ("ma5", "ma10")
    assert result.ma20 > result.ma60 > result.ma120


def test_taiya_retest_touches_ma5(taiya_df):
    subset = taiya_df[taiya_df.index <= SIGNAL_DATE]
    row = subset.iloc[-1]
    low = row["low"]
    ma5 = row["ma5"]
    assert abs(low - ma5) / ma5 <= 0.01, f"Low {low} 應接近 MA5 {ma5}"
