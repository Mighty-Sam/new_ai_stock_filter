"""K 線圖繪製。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle

CHART_BARS = 50

# 依平台常見順序排列（需通過 fontManager 實際存在才採用）
_CJK_FONT_CANDIDATES = [
    "Heiti TC",
    "Arial Unicode MS",
    "Hiragino Sans CNS",
    "PingFang HK",
    "Noto Sans CJK TC",
    "Noto Sans CJK JP",
    "WenQuanYi Micro Hei",
]

# macOS / Linux 系統字型路徑（fontManager 未索引時手動註冊）
_CJK_FONT_PATHS = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


@lru_cache(maxsize=1)
def _get_cjk_font() -> Optional[FontProperties]:
    """取得可用的繁體中文字型。"""
    available = {f.name for f in fm.fontManager.ttflist}
    for name in _CJK_FONT_CANDIDATES:
        if name in available:
            return FontProperties(family=name)

    for path_str in _CJK_FONT_PATHS:
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            fm.fontManager.addfont(str(path))
            return FontProperties(fname=str(path))
        except Exception:
            continue

    return None


def _setup_cjk_font() -> Optional[FontProperties]:
    font = _get_cjk_font()
    if font is None:
        return None

    family = font.get_name()
    plt.rcParams["font.sans-serif"] = [family, "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return font

MA_COLORS = {
    "ma5": "#FF9800",
    "ma10": "#00BCD4",
    "ma20": "#9C27B0",
    "ma60": "#4CAF50",
    "ma120": "#E91E63",
}

MA_LABELS = {
    "ma5": "MA5",
    "ma10": "MA10",
    "ma20": "MA20",
    "ma60": "MA60",
    "ma120": "MA120",
}


def plot_candlestick(
    df: pd.DataFrame,
    stock_code: str,
    stock_name: str,
    signal_date: Optional[pd.Timestamp] = None,
    output_path: Optional[Path] = None,
    bars: int = CHART_BARS,
    grade: Optional[str] = None,
    review_notes: Optional[list[str]] = None,
) -> Path:
    """繪製 K 線 + 均線 + 成交量圖，回傳 PNG 路徑。"""
    plot_df = df.tail(bars).copy()
    if plot_df.empty:
        raise ValueError("無資料可繪圖")

    out = output_path or Path("output") / f"{stock_code}.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    plt.style.use("dark_background")
    cjk_font = _setup_cjk_font()
    fig, (ax_price, ax_vol) = plt.subplots(
        2, 1, figsize=(12, 8), gridspec_kw={"height_ratios": [3, 1]}
    )

    dates = plot_df.index
    x = range(len(plot_df))

    for i, (_, row) in enumerate(plot_df.iterrows()):
        o, h, l, c = row["open"], row["high"], row["low"], row["close"]
        color = "#EF5350" if c >= o else "#26A69A"
        ax_price.plot([i, i], [l, h], color=color, linewidth=0.8)
        body_h = abs(c - o)
        body_b = min(o, c)
        rect = Rectangle(
            (i - 0.35, body_b),
            0.7,
            body_h if body_h > 0 else 0.01,
            facecolor=color,
            edgecolor=color,
            linewidth=0.5,
        )
        ax_price.add_patch(rect)

    for ma_col in ("ma5", "ma10", "ma20", "ma60", "ma120"):
        if ma_col in plot_df.columns:
            ax_price.plot(
                x,
                plot_df[ma_col],
                color=MA_COLORS[ma_col],
                linewidth=1.2,
                label=MA_LABELS[ma_col],
            )

    if signal_date is not None:
        sig_ts = pd.Timestamp(signal_date)
        if sig_ts in plot_df.index:
            sig_i = list(plot_df.index).index(sig_ts)
            ax_price.axvline(sig_i, color="white", linestyle="--", linewidth=1, alpha=0.8)
            ax_price.text(
                sig_i,
                plot_df["high"].max(),
                sig_ts.strftime("%Y/%m/%d"),
                color="white",
                fontsize=8,
                ha="center",
                va="bottom",
            )

    title_date = plot_df.index[-1].strftime("%Y/%m/%d")
    grade_tag = f" [{grade}級]" if grade else ""
    title = f"{stock_code} {stock_name}{grade_tag} — {title_date}"
    if cjk_font is not None:
        ax_price.set_title(title, fontsize=14, fontproperties=cjk_font)
    else:
        ax_price.set_title(title, fontsize=14)

    if review_notes:
        note_text = "\n".join(review_notes[:4])
        box_props = dict(boxstyle="round,pad=0.4", facecolor="#2a2a2a", alpha=0.85, edgecolor="#666")
        if cjk_font is not None:
            ax_price.text(
                0.02,
                0.02,
                note_text,
                transform=ax_price.transAxes,
                fontsize=7,
                color="#ddd",
                va="bottom",
                ha="left",
                fontproperties=cjk_font,
                bbox=box_props,
            )
        else:
            ax_price.text(
                0.02,
                0.02,
                note_text,
                transform=ax_price.transAxes,
                fontsize=7,
                color="#ddd",
                va="bottom",
                ha="left",
                bbox=box_props,
            )
    ax_price.set_ylabel("Price")
    ax_price.legend(loc="upper left", fontsize=8)
    ax_price.grid(True, alpha=0.2)

    vol_colors = [
        "#EF5350" if row["close"] >= row["open"] else "#26A69A"
        for _, row in plot_df.iterrows()
    ]
    ax_vol.bar(x, plot_df["volume"], color=vol_colors, alpha=0.7)
    ax_vol.set_ylabel("Volume")
    ax_vol.grid(True, alpha=0.2)

    step = max(1, len(plot_df) // 8)
    tick_idx = list(range(0, len(plot_df), step))
    tick_labels = [dates[i].strftime("%m/%d") for i in tick_idx]
    ax_vol.set_xticks(tick_idx)
    ax_vol.set_xticklabels(tick_labels, rotation=45)
    ax_price.set_xticks(tick_idx)
    ax_price.set_xticklabels([])

    plt.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="#1a1a1a")
    plt.close(fig)
    return out
