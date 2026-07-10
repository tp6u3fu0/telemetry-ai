"""兩圈比較圖：速度 / 油門煞車 / delta time 三個面板，共用 spline 橫軸。"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 中文標籤需要 CJK 字型（Windows 內建微軟正黑體）
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Segoe UI", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False
import numpy as np

from .compare import Comparison

# 調色（light mode）：色彩跟著「圈」走，線型跟著「量測」走
COLOR_A = "#2a78d6"      # 參考圈（藍）
COLOR_B = "#1baf7a"      # 比較圈（aqua）
COLOR_LOSS = "#d03b3b"   # delta 正值 = B 損失時間
COLOR_GAIN = "#006300"   # delta 負值 = B 賺到時間
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2ND = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"


def _style_axis(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.7)
    ax.tick_params(colors=MUTED, labelsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASELINE)


def plot_comparison(c: Comparison, out_path: str, worst_n: int = 3) -> str:
    fig, (ax_speed, ax_pedal, ax_delta) = plt.subplots(
        3, 1, figsize=(14, 9), sharex=True, facecolor=SURFACE,
        gridspec_kw={"height_ratios": [3, 2, 2], "hspace": 0.12})

    x = c.grid * 100  # spline %
    label_a, label_b = c.lap_a.label, c.lap_b.label

    # -- 面板 1：速度 --------------------------------------------------------
    ax_speed.plot(x, c.a["speed"], color=COLOR_A, linewidth=1.8, label=f"A  {label_a}")
    ax_speed.plot(x, c.b["speed"], color=COLOR_B, linewidth=1.8, label=f"B  {label_b}")
    ax_speed.set_ylabel("速度 (km/h)", color=INK_2ND, fontsize=10)
    ax_speed.legend(loc="lower left", frameon=False, fontsize=9, labelcolor=INK)
    # 直接標示線尾（兩線太接近就跳過，避免重疊）
    end_a, end_b = c.a["speed"][-1], c.b["speed"][-1]
    if abs(end_a - end_b) > (ax_speed.get_ylim()[1] - ax_speed.get_ylim()[0]) * 0.03:
        ax_speed.annotate("A", (x[-1], end_a), color=COLOR_A,
                          fontsize=10, fontweight="bold", xytext=(4, 0),
                          textcoords="offset points", va="center")
        ax_speed.annotate("B", (x[-1], end_b), color=COLOR_B,
                          fontsize=10, fontweight="bold", xytext=(4, 0),
                          textcoords="offset points", va="center")

    # -- 面板 2：油門（實線）與煞車（虛線），顏色跟著圈 ----------------------
    ax_pedal.plot(x, c.a["throttle"] * 100, color=COLOR_A, linewidth=1.4)
    ax_pedal.plot(x, c.b["throttle"] * 100, color=COLOR_B, linewidth=1.4)
    ax_pedal.plot(x, c.a["brake"] * 100, color=COLOR_A, linewidth=1.4, linestyle="--")
    ax_pedal.plot(x, c.b["brake"] * 100, color=COLOR_B, linewidth=1.4, linestyle="--")
    ax_pedal.set_ylabel("油門 — / 煞車 ­-­- (%)", color=INK_2ND, fontsize=10)
    ax_pedal.set_ylim(-5, 105)

    # -- 面板 3：delta time（正 = B 較慢） -----------------------------------
    delta_s = c.delta_ms / 1000.0
    ax_delta.axhline(0, color=BASELINE, linewidth=1.0)
    ax_delta.plot(x, delta_s, color=INK_2ND, linewidth=1.6)
    ax_delta.fill_between(x, delta_s, 0, where=delta_s >= 0,
                          color=COLOR_LOSS, alpha=0.18, linewidth=0)
    ax_delta.fill_between(x, delta_s, 0, where=delta_s < 0,
                          color=COLOR_GAIN, alpha=0.18, linewidth=0)
    ax_delta.set_ylabel("Δt (s)  正=B較慢", color=INK_2ND, fontsize=10)
    ax_delta.set_xlabel("賽道位置 (spline %)", color=INK_2ND, fontsize=10)

    # 標出損失最大的幾個煞車區段
    worst = sorted(c.zones, key=lambda z: -abs(z.time_lost_ms))[:worst_n]
    for z in worst:
        for ax in (ax_speed, ax_pedal, ax_delta):
            ax.axvspan(z.start * 100, z.end * 100, color=MUTED, alpha=0.08, linewidth=0)
        mid = (z.start + z.end) / 2 * 100
        y = float(np.interp(mid, x, delta_s))
        lo, hi = float(delta_s.min()), float(delta_s.max())
        near_top = hi - lo > 0 and (y - lo) / (hi - lo) > 0.75
        ax_delta.annotate(
            f"#{z.index}  {z.time_lost_ms/1000:+.2f}s",
            (mid, y), color=INK, fontsize=9, ha="center",
            xytext=(0, -16 if near_top else 12), textcoords="offset points",
            annotation_clip=True)

    for ax in (ax_speed, ax_pedal, ax_delta):
        _style_axis(ax)

    fig.suptitle(f"圈次比較  A: {label_a}  vs  B: {label_b}   "
                 f"(總差 {c.total_delta_ms/1000:+.3f}s)",
                 color=INK, fontsize=12, y=0.98)
    fig.savefig(out_path, dpi=130, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return out_path
