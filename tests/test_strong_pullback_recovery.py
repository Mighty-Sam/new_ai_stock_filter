"""強勢股長黑洗盤後快速收復均線（SPR）單元測試。"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.screener import strong_pullback_recovery as spr
from src.screener.strong_pullback_recovery import evaluate_spr, find_events, prepare_frame

FIXTURE = Path(__file__).parent / "fixtures" / "spr_6213.csv"
T0 = 80


def _bar(o: float, c: float, vol: float = 1e6) -> dict:
    return {"open": o, "high": max(o, c) + 0.5, "low": min(o, c) - 0.5, "close": c, "volume": vol}


def _pattern(t0_close: float = 140.0, recovery=(142.0, 152.0), tail: int = 0, base: int = 40) -> pd.DataFrame:
    """平盤 → 20 根拉到 150 → 20 根緊密整理 → T0 長黑（量 3 倍）→ 收復。"""
    rows = [_bar(99.5 if i % 2 else 100.0, 100.0 if i % 2 else 99.5) for i in range(base)]
    prev = 100.0
    for i in range(20):
        c = 100.0 + 2.5 * (i + 1)
        rows.append(_bar(prev, c))
        prev = c
    for i in range(20):
        rows.append(_bar(149.0, 149.5) if i % 2 else _bar(149.5, 149.0))
    rows.append({"open": 149.0, "high": 149.5, "low": t0_close - 0.5, "close": t0_close, "volume": 3e6})
    prev = t0_close
    for c in recovery:
        rows.append(_bar(prev + 0.5, c, 2e6))
        prev = c
    for _ in range(tail):
        rows.append(_bar(prev, prev + 0.5))
        prev += 0.5
    df = pd.DataFrame(rows)
    df.index = pd.bdate_range("2025-01-02", periods=len(df))
    return df


def _t0(base: int = 40) -> int:
    return base + 40


def _6213() -> pd.DataFrame:
    df = pd.read_csv(FIXTURE, parse_dates=["date"]).set_index("date")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def test_signal_on_recovery_day() -> None:
    df = _pattern(base=100)  # 足夠的資料根數（≥120）
    res = evaluate_spr(df, "9999")
    sig = [r for r in res if r.kind == "signal"]
    assert len(sig) == 1
    r = sig[0]
    assert r.t0_date == df.index[_t0(100)] and r.recovery_days == 2
    assert r.body_multiple == pytest.approx(18.0) and r.volume_ratio == pytest.approx(3.0)
    assert r.volume_tag == "放量長黑" and r.main_list


def test_watch_on_t0_day_has_no_recovery_points() -> None:
    df = _pattern(recovery=(), base=100)
    res = evaluate_spr(df, "9999")
    assert [r.kind for r in res] == ["watch"]
    assert res[0].components["收復"] == 0 and res[0].recovery_days is None


def test_cluster_uses_previous_day_mas() -> None:
    f = prepare_frame(_pattern())
    t0 = _t0()
    assert f["cluster"].iloc[t0 - 1] < 0.01
    assert bool(f["long_black"].iloc[t0])


def test_not_black_rejected() -> None:
    df = _pattern()
    df.iloc[_t0(), df.columns.get_loc("open")] = 139.0
    assert all(e.t0 != _t0() for e in find_events(prepare_frame(df)))


def test_no_recovery_within_three_days() -> None:
    df = _pattern(recovery=(141.0, 141.5, 142.0, 155.0))
    ev = [e for e in find_events(prepare_frame(df)) if e.t0 == _t0()]
    assert ev and ev[0].signal is None


def test_intraday_break_does_not_count() -> None:
    df = _pattern(recovery=(142.0, 143.0, 144.0))
    df.iloc[_t0() + 2, df.columns.get_loc("high")] = 170.0
    ev = [e for e in find_events(prepare_frame(df)) if e.t0 == _t0()]
    assert ev and ev[0].signal is None


def test_zero_range_and_zero_average_body() -> None:
    df = _pattern()
    for i in range(_t0() - 20, _t0()):
        df.iloc[i, :4] = [149.0, 149.0, 149.0, 149.0]
    f = prepare_frame(df)
    assert math.isnan(f["body_ratio"].iloc[_t0() - 1])
    assert math.isnan(f["body_multiple"].iloc[_t0()])
    assert not bool(f["long_black"].iloc[_t0()])


def test_future_bars_do_not_change_past_decision() -> None:
    df = _pattern(tail=10, base=100)
    day = df.index[_t0(100) + 2]
    a = evaluate_spr(df.loc[:day], "9999")
    poisoned = df.copy()
    poisoned.loc[poisoned.index > day, ["open", "high", "low", "close"]] *= 10
    b = evaluate_spr(poisoned.loc[:day], "9999")
    assert [(r.kind, r.score) for r in a] == [(r.kind, r.score) for r in b]


def test_follow_up_after_signal() -> None:
    df = _pattern(tail=3, base=100)
    res = evaluate_spr(df, "9999")
    fu = [r for r in res if r.kind == "follow_up"]
    assert len(fu) == 1 and fu[0].held_ma20 and fu[0].held_t0_low and fu[0].follow_days == 3


def test_etf_and_non_common_codes_skipped() -> None:
    df = _pattern(base=100)
    assert evaluate_spr(df, "0050") == [] and evaluate_spr(df, "12345") == []


def test_6213_watch_then_signal() -> None:
    df = _6213()
    watch = [r for r in evaluate_spr(df.loc[:"2026-09-17"], "6213") if r.kind == "watch"]
    assert len(watch) == 1
    w = watch[0]
    assert w.t0_close == 495.0 and w.volume_ratio == pytest.approx(2.093, abs=1e-3)
    assert w.cluster_prev == pytest.approx(0.0191, abs=1e-4)
    assert "limit_down_t0" in w.risk_flags
    sig = [r for r in evaluate_spr(df.loc[:"2026-09-21"], "6213") if r.kind == "signal"]
    assert len(sig) == 1
    s = sig[0]
    assert s.recovery_days == 2 and s.close == 542.0
    assert s.ma5 == pytest.approx(522.2) and s.ma20 == pytest.approx(531.05)
    assert s.held_ma20 is None  # 後續確認不出現在訊號列


def test_6213_not_found_long_before_t0() -> None:
    df = _6213()
    assert evaluate_spr(df.loc[:"2026-09-10"], "6213") == []


def test_score_breakdown_sums_to_total() -> None:
    sig = [r for r in evaluate_spr(_6213().loc[:"2026-09-21"], "6213") if r.kind == "signal"][0]
    assert sum(sig.components.values()) == pytest.approx(sig.score, abs=0.05)
    assert sig.grade in {"A", "B", "C"}


def test_limit_threshold_approximation() -> None:
    assert spr.SPR_LIMIT_MOVE < 0.10
    assert np.isclose(spr.SPR_GRADES[0][1], 80.0)


def test_telegram_summary_format() -> None:
    from src.notify.telegram_client import TelegramClient

    df = _6213()
    sig = [r for r in evaluate_spr(df.loc[:"2026-09-21"], "6213") if r.kind == "signal"]
    text = TelegramClient(bot_token="x", chat_id="y").format_spr_summary(sig, [], [], {"6213": "聯茂"}, "2026/09/21")
    assert "強勢股長黑收復均線" in text and "6213 聯茂" in text and "放量長黑" in text
    assert "回踩參考 MA5 522.20／MA10 518.70" in text and "T0 收跌停" in text and "未顯示顯著優勢" in text
    empty = TelegramClient(bot_token="x", chat_id="y").format_spr_summary([], [], [], {}, "2026/06/01")
    assert "今日無符合條件個股" in empty
