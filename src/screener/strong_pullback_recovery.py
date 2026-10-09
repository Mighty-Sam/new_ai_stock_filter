"""強勢股長黑洗盤後快速收復均線（Strong Trend Pullback Recovery, SPR）。

型態：前段強勢上漲 → MA5/10/20 糾結 → 異常長黑 K 跌破三線 → 1～3 個交易日內收盤站回三線。

移植自 tw-theme-radar（radar.patterns），規則與預設參數相同，研究與回測都在該 repo：
- 研究報告 docs/pattern_spr_research.md：事先登記的回測（2019-07～2026-09）**未顯示顯著優勢**，本策略僅供觀察。
- 量縮長黑（T0 量比 < 0.8）是三期都最弱的一組，推播時會標註。

時間點（避免未來資訊）：
- T0 = 長黑 K 日；糾結用 T0 前一日的 MA，不使用 T0 當天的 MA。
- Signal = 首次「收盤」同時高於當日 MA5/10/20 的日子（盤中突破不算）；最早可交易時點為次一交易日開盤。
- 後續確認（守住 MA20、未再跌破 T0 低點）只用訊號之後的 K 棒，另列為 follow-up，不回填訊號分數。

資料差異（相對 tw-theme-radar）：
- 價格來自 PriceFetcher（實務上為 yfinance 還原價），成交金額以 close × volume 近似。
- 沒有新聞、重大訊息、處置與全額交割資料：這些風險一律視為「未檢查」，評分的對應 2 分為 0（滿分實際 98）。
- 漲跌停以 ±9.5% 漲跌幅近似判斷。
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.data.price_fetcher import PriceFetcher
from src.screener.scan_runner import ScanRun, run_market_scan

logger = logging.getLogger(__name__)

# ---- 型態參數（研究規格第一版；與 tw-theme-radar config/pattern_spr.yaml 相同）
SPR_LOOKBACK_BARS = 50  # 前段強勢回看根數
SPR_MIN_GAIN = 0.30  # 波段高點 / 波段低點 - 1
SPR_MAX_BARS_SINCE_HIGH = 10  # T0 距波段高點根數上限
SPR_CLUSTER_MAX = 0.05  # T0 前一日 (max MA - min MA) / close
SPR_CLUSTER_HQ = 0.03
SPR_AVG_WINDOW = 20  # 平均實體 / 平均量的回看根數（T0-20 .. T0-1）
SPR_MIN_BODY_MULTIPLE = 1.8
SPR_MIN_BODY_RATIO = 0.60
SPR_STRONG_CLOSE_LOCATION = 0.25
SPR_VOLUME_ABNORMAL = 1.5
SPR_VOLUME_STRONG = 2.0
SPR_RECOVERY_MAX_DAYS = 3
SPR_FOLLOW_UP_DAYS = 3

# ---- 標的範圍
SPR_MIN_PRICE = 10.0
SPR_MIN_TURNOVER_20 = 2.0e7  # 20 日平均成交金額（元，close × volume 近似）
SPR_MIN_VOLUME_20 = 100_000  # 20 日平均成交量（股，= 100 張）
SPR_MIN_BARS = 120  # 上市 / 資料至少 120 根
_COMMON_STOCK = re.compile(r"^\d{4}$")

# ---- 風險與標註
SPR_LOW_VOLUME_TAG = 0.8  # T0 量比低於此值 → 量縮長黑（研究中三期最弱的一組）
SPR_HIGH_VOLUME_TAG = 1.2  # 高於等於 → 放量長黑
SPR_PRE_DOWN_WINDOW = 5
SPR_PRE_DOWN_VOLUME_RATIO = 1.2
SPR_PRE_DOWN_MIN_DAYS = 3
SPR_MA20_DOWN_SLOPE = -0.01
SPR_LOW_RECOVERY_VOLUME_RATIO = 0.8
SPR_GAP_THRESHOLD = 0.05
SPR_LOW_LIQUIDITY_TURNOVER_20 = 5.0e7
SPR_LIMIT_MOVE = 0.095  # 漲跌停近似（±10% 減 tick 誤差）

SPR_GRADES = (("A", 80.0), ("B", 65.0), ("C", 50.0))
SPR_NOTE = "研究型態：事先登記的回測未顯示顯著優勢，僅供觀察，非買進訊號"

RISK_LABELS = {
    "pre_down_volume": "長黑前多日放量下跌",
    "broke_prior_low": "長黑跌破整理低點",
    "ma20_falling": "MA20 明顯向下",
    "gap_t0": "T0 跳空",
    "limit_down_t0": "T0 收跌停",
    "low_liquidity": "流動性偏低",
    "low_recovery_volume": "收復日量縮",
    "gap_signal": "收復日跳空",
    "limit_up_signal": "收復日收漲停（次日可能買不到）",
    "unexplained_gap": "異常缺口",
    "broke_ma20_after": "收復後跌破 MA20",
    "broke_t0_low_after": "收復後跌破 T0 低點",
}


@dataclass
class SPRResult:
    stock_code: str
    kind: str  # "signal" | "watch" | "follow_up"
    bar_date: pd.Timestamp  # 該列的決策日：signal / follow_up = 訊號日，watch = T0
    t0_date: pd.Timestamp
    t0_open: float
    t0_high: float
    t0_low: float
    t0_close: float
    swing_low_date: pd.Timestamp
    swing_low: float
    swing_high_date: pd.Timestamp
    swing_high: float
    swing_gain: float
    bars_since_high: int
    cluster_prev: float
    body_multiple: float
    body_ratio: float
    close_location: float
    volume_ratio: float  # T0 量 / 前 20 日均量
    turnover_ratio: float
    volume_tag: str  # 量縮長黑 / 量平 / 放量長黑
    score: float
    grade: str
    components: Dict[str, float]
    risk_flags: List[str]
    main_list: bool  # 通過標的範圍門檻且分數 ≥ C 級
    excluded: List[str]
    recovery_days: Optional[int] = None
    close: Optional[float] = None  # 訊號日收盤（watch 為 T0 收盤）
    ma5: Optional[float] = None  # 訊號日 MA5（回踩參考）
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    signal_volume_ratio: Optional[float] = None
    held_ma20: Optional[bool] = None  # follow-up 才有
    held_t0_low: Optional[bool] = None
    follow_days: int = 0
    review_notes: List[str] = field(default_factory=list)

    @property
    def signal_date(self) -> pd.Timestamp:  # 與其他策略相同的屬性名稱（繪圖標記用）
        return self.bar_date


@dataclass
class SPRScanOutput:
    signals: List[SPRResult]
    watch: List[SPRResult]
    follow_up: List[SPRResult]
    price_data: Dict[str, pd.DataFrame]
    scan_date: date
    is_trading_day: bool

    @property
    def results(self) -> List[SPRResult]:
        return self.signals


# ---------------------------------------------------------------- primitives


def _lin(x: float, zero: float, full: float, points: float) -> float:
    """x == zero → 0，x == full → points，中間線性、兩端截斷；可反向（zero > full 表示越小越好）。"""
    if x is None or math.isnan(x):
        return 0.0
    t = (x - zero) / (full - zero)
    return points * min(1.0, max(0.0, t))


def _band(x: float, zero_lo: float, full_lo: float, full_hi: float, zero_hi: float, points: float) -> float:
    if x is None or math.isnan(x):
        return 0.0
    if full_lo <= x <= full_hi:
        return points
    if x < full_lo:
        return 0.0 if x <= zero_lo else points * (x - zero_lo) / (full_lo - zero_lo)
    return 0.0 if x >= zero_hi else points * (zero_hi - x) / (zero_hi - full_hi)


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    """num / den；den 為 0 或 NaN 時回傳 NaN（不除以零）。"""
    return num / den.where(den > 0)


def prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    """補上所有逐根、只用當根以前資料的欄位。"""
    f = df[["open", "high", "low", "close", "volume"]].astype(float).copy()
    o, h, lo, c, v = f["open"], f["high"], f["low"], f["close"], f["volume"]
    for w in (5, 10, 20):
        f[f"ma{w}"] = c.rolling(w, min_periods=w).mean()
    mas = f[["ma5", "ma10", "ma20"]]
    f["ma_max"] = mas.max(axis=1, skipna=False)
    f["ma_min"] = mas.min(axis=1, skipna=False)
    f["cluster"] = _ratio(f["ma_max"] - f["ma_min"], c)
    rng = h - lo
    f["body"] = (c - o).abs()
    f["body_ratio"] = _ratio(f["body"], rng)
    f["close_location"] = _ratio(c - lo, rng)
    f["avg_body"] = f["body"].shift(1).rolling(SPR_AVG_WINDOW, min_periods=SPR_AVG_WINDOW).mean()
    f["body_multiple"] = _ratio(f["body"], f["avg_body"])
    f["turnover"] = c * v
    f["avg_volume"] = v.shift(1).rolling(SPR_AVG_WINDOW, min_periods=SPR_AVG_WINDOW).mean()
    f["avg_turnover"] = f["turnover"].shift(1).rolling(SPR_AVG_WINDOW, min_periods=SPR_AVG_WINDOW).mean()
    f["avg_volume_5"] = v.shift(1).rolling(5, min_periods=5).mean()
    f["volume_ratio"] = _ratio(v, f["avg_volume"])
    f["turnover_ratio"] = _ratio(f["turnover"], f["avg_turnover"])
    f["ret"] = _ratio(c, c.shift(1)) - 1
    f["gap"] = _ratio(o, c.shift(1)) - 1
    f["above_all"] = c > f["ma_max"]
    f["long_black"] = (
        (c < o)
        & (f["body_multiple"] >= SPR_MIN_BODY_MULTIPLE)
        & (f["body_ratio"] >= SPR_MIN_BODY_RATIO)
        & (c < f["ma_min"])
        & (f["cluster"].shift(1) <= SPR_CLUSTER_MAX)
    )
    return f


@dataclass(frozen=True)
class _Swing:
    low_pos: int
    low: float
    high_pos: int
    high: float

    @property
    def gain(self) -> float:
        return self.high / self.low - 1


def find_swing(high: np.ndarray, low: np.ndarray, t0: int) -> Optional[_Swing]:
    """固定窗口波段：T0 前 50 根內，距 T0 不超過 10 根的每一根都可當波段高點，
    低點取窗口起點到該高點之間的最低點，取漲幅最大的一組。只看 T0 之前的 K 棒。"""
    start = max(0, t0 - SPR_LOOKBACK_BARS)
    if t0 - start < 2:
        return None
    h, lo = high[start:t0], low[start:t0]
    if np.isnan(h).any() or np.isnan(lo).any():
        return None
    run_min = np.minimum.accumulate(lo)
    ratio = h / run_min
    first = max(0, len(h) - SPR_MAX_BARS_SINCE_HIGH)
    best = first + int(np.argmax(ratio[first:]))
    low_rel = int(np.argmin(lo[: best + 1]))
    if low_rel == best:
        return None
    return _Swing(start + low_rel, float(lo[low_rel]), start + best, float(h[best]))


@dataclass(frozen=True)
class _Event:
    t0: int
    swing: _Swing
    signal: Optional[int]


def find_events(f: pd.DataFrame, first_t0: int = 0) -> List[_Event]:
    """所有 T0 ≥ first_t0 且前三階段成立的事件，附收復日（尚未收復則為 None）。"""
    long_black = f["long_black"].to_numpy(bool)
    above = f["above_all"].to_numpy(bool)
    hi, lo = f["high"].to_numpy(float), f["low"].to_numpy(float)
    n = len(f)
    need = SPR_LOOKBACK_BARS + SPR_AVG_WINDOW
    out: List[_Event] = []
    for t0 in (int(x) for x in np.flatnonzero(long_black)):
        if t0 < first_t0 or t0 < need:
            continue
        sw = find_swing(hi, lo, t0)
        if sw is None or sw.gain < SPR_MIN_GAIN:
            continue
        signal = None
        for t in range(t0 + 1, min(t0 + SPR_RECOVERY_MAX_DAYS, n - 1) + 1):
            if above[t]:
                signal = t
                break
        out.append(_Event(t0, sw, signal))
    return out


def _volume_tag(vr: float) -> str:
    if vr < SPR_LOW_VOLUME_TAG:
        return "量縮長黑"
    if vr >= SPR_HIGH_VOLUME_TAG:
        return "放量長黑"
    return "量平"


def _grade(total: float) -> str:
    for name, threshold in SPR_GRADES:
        if total >= threshold:
            return name
    return "-"


def score_components(feats: Dict[str, float]) -> Dict[str, float]:
    """100 分制評分（第一版研究假設，與 tw-theme-radar 相同公式）。缺資料的項目給 0。"""
    g = feats.get
    rec_days = g("recovery_days")
    has_rec = rec_days is not None and not math.isnan(rec_days)
    comps = {
        "前段漲幅": _lin(g("swing_gain"), 0.30, 0.80, 12)
        + _band(g("pullback_from_high"), 0.0, 0.05, 0.20, 0.35, 5)
        + (3.0 if g("half_retrace_held") == 1.0 else 0.0),
        "均線糾結": _lin(g("cluster_prev"), 0.05, 0.03, 10) + _lin(g("ma20_slope_prev"), -0.005, 0.01, 5),
        "長黑K": _lin(g("body_multiple"), 1.8, 3.5, 8) + _lin(g("body_ratio"), 0.6, 0.9, 6) + _lin(g("close_location"), 0.5, 0.25, 6),
        "成交量": _lin(g("volume_ratio"), 1.0, 3.0, 10) + _lin(g("turnover_ratio"), 1.0, 3.0, 5),
        "收復": 0.0,
        "流動性": _lin(g("log10_turnover_20"), 7.5, 9.0, 6) + float(g("dq_complete") or 0) + float(g("dq_no_gap") or 0),
    }
    if has_rec:
        speed = {1: 8.0, 2: 5.0, 3: 2.0}.get(int(rec_days), 0.0)
        comps["收復"] = speed + sum(3.0 for k in ("rec_close_above_open", "rec_upper_half", "rec_volume_above_5", "rec_close_above_t0_high") if g(k) == 1.0)
    return {k: round(v, 2) for k, v in comps.items()}


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def _build_result(stock_code: str, f: pd.DataFrame, ev: _Event, kind: str, as_of_pos: int) -> SPRResult:
    t0, sw = ev.t0, ev.swing
    use_signal = kind in ("signal", "follow_up") and ev.signal is not None
    pos = ev.signal if use_signal else t0
    r0, rp = f.iloc[t0], f.iloc[t0 - 1]
    close = f["close"].to_numpy(float)
    lo = f["low"].to_numpy(float)
    cons = lo[sw.high_pos + 1 : t0]
    cons_low = float(np.nanmin(cons)) if len(cons) and not np.isnan(cons).all() else float("nan")
    ma20 = f["ma20"].to_numpy(float)
    ma20_slope = ma20[t0 - 1] / ma20[t0 - 6] - 1 if t0 >= 6 and ma20[t0 - 6] > 0 else float("nan")
    vr = _f(r0["volume_ratio"])
    window = f.iloc[t0 - SPR_LOOKBACK_BARS - SPR_AVG_WINDOW : pos + 1]
    dq_complete = bool(window["close"].notna().all())
    dq_no_gap = not bool((window["gap"].abs() > 0.105).fillna(False).any())
    feats: Dict[str, float] = {
        "swing_gain": sw.gain,
        "pullback_from_high": 1 - close[t0 - 1] / sw.high,
        "half_retrace_held": float(cons_low >= sw.low + 0.5 * (sw.high - sw.low)) if not math.isnan(cons_low) else float("nan"),
        "cluster_prev": _f(rp["cluster"]),
        "ma20_slope_prev": ma20_slope,
        "body_multiple": _f(r0["body_multiple"]),
        "body_ratio": _f(r0["body_ratio"]),
        "close_location": _f(r0["close_location"]),
        "volume_ratio": vr,
        "turnover_ratio": _f(r0["turnover_ratio"]),
        "dq_complete": float(dq_complete),
        "dq_no_gap": float(dq_no_gap),
    }
    rs = f.iloc[pos]
    turnover20 = _f(rs["avg_turnover"])
    feats["log10_turnover_20"] = math.log10(turnover20) if turnover20 > 0 else float("nan")
    recovery_days = None
    if use_signal:
        recovery_days = int(pos - t0)
        loc = _f(rs["close_location"])
        feats.update(
            {
                "recovery_days": float(recovery_days),
                "rec_close_above_open": float(_f(rs["close"]) > _f(rs["open"])),
                "rec_upper_half": float(loc >= 0.5) if not math.isnan(loc) else float("nan"),
                "rec_volume_above_5": float(_f(rs["volume"]) > _f(rs["avg_volume_5"])),
                "rec_close_above_t0_high": float(_f(rs["close"]) > _f(r0["high"])),
            }
        )
    comps = score_components(feats)
    total = round(sum(comps.values()), 2)
    grade = _grade(total)

    flags: List[str] = []
    down = (f["close"].diff() < 0).to_numpy(bool)
    heavy = (f["volume_ratio"] >= SPR_PRE_DOWN_VOLUME_RATIO).to_numpy(bool)
    if int((down & heavy)[t0 - SPR_PRE_DOWN_WINDOW : t0].sum()) >= SPR_PRE_DOWN_MIN_DAYS:
        flags.append("pre_down_volume")
    if not math.isnan(cons_low) and close[t0] < cons_low:
        flags.append("broke_prior_low")
    if ma20_slope < SPR_MA20_DOWN_SLOPE:
        flags.append("ma20_falling")
    if abs(_f(r0["gap"])) >= SPR_GAP_THRESHOLD:
        flags.append("gap_t0")
    if _f(r0["ret"]) <= -SPR_LIMIT_MOVE:
        flags.append("limit_down_t0")
    if turnover20 < SPR_LOW_LIQUIDITY_TURNOVER_20:
        flags.append("low_liquidity")
    if use_signal:
        if _f(rs["volume_ratio"]) < SPR_LOW_RECOVERY_VOLUME_RATIO:
            flags.append("low_recovery_volume")
        if abs(_f(rs["gap"])) >= SPR_GAP_THRESHOLD:
            flags.append("gap_signal")
        if _f(rs["ret"]) >= SPR_LIMIT_MOVE:
            flags.append("limit_up_signal")
    if not dq_no_gap:
        flags.append("unexplained_gap")

    excluded: List[str] = []
    if not _f(rs["close"]) >= SPR_MIN_PRICE:
        excluded.append("股價")
    if not turnover20 >= SPR_MIN_TURNOVER_20:
        excluded.append("成交金額")
    if not _f(rs["avg_volume"]) >= SPR_MIN_VOLUME_20:
        excluded.append("成交量")
    if int(f["close"].iloc[: pos + 1].notna().sum()) < SPR_MIN_BARS:
        excluded.append("資料根數")
    if not dq_complete:
        excluded.append("資料不完整")
    if not dq_no_gap:
        excluded.append("異常缺口")

    held_ma20 = held_t0_low = None
    follow_days = 0
    follow_flags: List[str] = []
    if kind == "follow_up" and ev.signal is not None:
        last = min(as_of_pos, ev.signal + SPR_FOLLOW_UP_DAYS)
        win = f.iloc[ev.signal + 1 : last + 1]
        follow_days = len(win)
        held_ma20 = bool((win["close"] >= win["ma20"]).all())
        held_t0_low = bool((win["close"] >= _f(r0["low"])).all())
        if not held_ma20:
            follow_flags.append("broke_ma20_after")
        if not held_t0_low:
            follow_flags.append("broke_t0_low_after")
        flags = flags + follow_flags

    notes = [
        f"前段波段 {f.index[sw.low_pos]:%m/%d} {sw.low:.2f} → {f.index[sw.high_pos]:%m/%d} {sw.high:.2f}（+{sw.gain * 100:.0f}%，距 T0 {t0 - sw.high_pos} 根）",
        f"T0 {f.index[t0]:%m/%d} 前一日均線糾結 {feats['cluster_prev'] * 100:.1f}%",
        f"長黑 實體 {feats['body_multiple']:.2f} 倍、占振幅 {feats['body_ratio'] * 100:.0f}%、量比 {vr:.2f}（{_volume_tag(vr)}）",
    ]
    if use_signal:
        notes.append(f"{recovery_days} 日收復，收 {_f(rs['close']):.2f} > MA5/10/20")
        notes.append(f"回踩參考 MA5 {_f(rs['ma5']):.2f}／MA10 {_f(rs['ma10']):.2f}｜T0 低 {_f(r0['low']):.2f}")
    notes.append("⚠️ " + SPR_NOTE)

    return SPRResult(
        stock_code=stock_code,
        kind=kind,
        bar_date=f.index[pos],
        t0_date=f.index[t0],
        t0_open=round(_f(r0["open"]), 2),
        t0_high=round(_f(r0["high"]), 2),
        t0_low=round(_f(r0["low"]), 2),
        t0_close=round(_f(r0["close"]), 2),
        swing_low_date=f.index[sw.low_pos],
        swing_low=round(sw.low, 2),
        swing_high_date=f.index[sw.high_pos],
        swing_high=round(sw.high, 2),
        swing_gain=round(sw.gain, 4),
        bars_since_high=t0 - sw.high_pos,
        cluster_prev=round(feats["cluster_prev"], 4),
        body_multiple=round(feats["body_multiple"], 3),
        body_ratio=round(feats["body_ratio"], 3),
        close_location=round(feats["close_location"], 3),
        volume_ratio=round(vr, 3),
        turnover_ratio=round(feats["turnover_ratio"], 3),
        volume_tag=_volume_tag(vr),
        score=total,
        grade=grade,
        components=comps,
        risk_flags=flags,
        main_list=not excluded and grade != "-",
        excluded=excluded,
        recovery_days=recovery_days,
        close=round(_f(rs["close"]), 2),
        ma5=round(_f(rs["ma5"]), 2),
        ma10=round(_f(rs["ma10"]), 2),
        ma20=round(_f(rs["ma20"]), 2),
        signal_volume_ratio=round(_f(rs["volume_ratio"]), 3) if use_signal else None,
        held_ma20=held_ma20,
        held_t0_low=held_t0_low,
        follow_days=follow_days,
        review_notes=notes,
    )


def evaluate_spr(df: pd.DataFrame, stock_code: str = "") -> List[SPRResult]:
    """以 df 最後一根 K 棒為決策日，回傳 signal / watch / follow_up 結果（df 須只含決策日以前的資料）。"""
    if stock_code and (not _COMMON_STOCK.match(stock_code) or stock_code.startswith("00")):
        return []
    if df is None or len(df) < SPR_LOOKBACK_BARS + SPR_AVG_WINDOW + 2:
        return []
    f = prepare_frame(df)
    pos = len(f) - 1
    events = find_events(f, first_t0=max(0, pos - SPR_RECOVERY_MAX_DAYS - SPR_FOLLOW_UP_DAYS))
    out: List[SPRResult] = []
    latest_by_signal: Dict[int, _Event] = {}
    for ev in events:
        if ev.t0 == pos:
            out.append(_build_result(stock_code, f, ev, "watch", pos))
        elif ev.signal is not None:
            latest_by_signal[ev.signal] = ev  # 同一收復日對應多根長黑時，取最近一根
    for s, ev in latest_by_signal.items():
        if s == pos:
            out.append(_build_result(stock_code, f, ev, "signal", pos))
        elif pos - SPR_FOLLOW_UP_DAYS <= s < pos:
            out.append(_build_result(stock_code, f, ev, "follow_up", pos))
    return out


def _process_spr_stock(
    stock_code: str,
    fetcher: PriceFetcher,
    end_date: Optional[date] = None,
) -> Tuple[str, Optional[List[SPRResult]], Optional[pd.DataFrame]]:
    df = fetcher.fetch(stock_code, end_date=end_date)
    if df is None:
        return stock_code, None, None
    results = evaluate_spr(df, stock_code=stock_code)
    return stock_code, (results or None), df


def scan_strong_pullback_recovery(
    max_workers: int = 8,
    stock_limit: Optional[int] = None,
    end_date: Optional[date] = None,
    trading_day: Optional[bool] = None,
) -> SPRScanOutput:
    run: ScanRun[List[SPRResult]] = run_market_scan(
        partial(_process_spr_stock, end_date=end_date),
        max_workers=max_workers,
        stock_limit=stock_limit,
        end_date=end_date,
        desc="長黑收復均線掃描",
        trading_day=trading_day,
    )
    flat = [r for group in run.results for r in group]
    by_score = lambda rs: sorted(rs, key=lambda r: -r.score)  # noqa: E731
    signals = by_score([r for r in flat if r.kind == "signal"])
    watch = by_score([r for r in flat if r.kind == "watch"])
    follow = by_score([r for r in flat if r.kind == "follow_up"])
    logger.info("長黑收復均線：signal %d / watch %d / follow-up %d（共 %d 檔）", len(signals), len(watch), len(follow), run.total_codes)
    return SPRScanOutput(signals, watch, follow, run.price_data, run.scan_date, run.is_trading_day)


def format_risk_flags(flags: List[str]) -> str:
    return "、".join(RISK_LABELS.get(x, x) for x in flags)
