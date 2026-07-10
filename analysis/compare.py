"""兩圈比較：delta time 曲線 + 煞車區段分析 + 文字摘要（階段四的 LLM context 來源）。

慣例：lap A = 參考圈（通常是最快圈），lap B = 被比較的圈。
delta_ms(s) = B 在位置 s 的累計時間 − A 的累計時間；正值 = B 比較慢。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .loader import LapTrace, common_grid, resample_to_grid

BRAKE_THRESHOLD = 0.10   # 視為「在煞車」的踏板深度
ZONE_MERGE_GAP = 0.01    # 兩煞車區段間距小於賽道 1% 就合併


@dataclass
class BrakeZone:
    index: int               # 第幾個煞車區段（照賽道順序，1 起算）
    start: float             # 區段起點 spline（含前置緩衝）
    end: float
    brake_on_a: float        # A 開始煞車的 spline 位置
    brake_on_b: float
    min_speed_a: float       # 彎中最低速
    min_speed_b: float
    exit_speed_a: float      # 區段出口速度
    exit_speed_b: float
    time_lost_ms: float      # B 在此區段損失（負值 = B 反而賺）
    apex: float              # A 的最低速位置（spline）
    entry_loss_ms: float     # 相位拆解：煞車進彎段（區段起點 → apex）
    exit_loss_ms: float      # 相位拆解：出彎加速段（apex → 下一煞車點）


@dataclass
class Comparison:
    lap_a: LapTrace
    lap_b: LapTrace
    grid: np.ndarray         # spline 網格
    a: dict                  # resample 後各通道
    b: dict
    delta_ms: np.ndarray     # 正 = B 較慢
    zones: list = field(default_factory=list)

    @property
    def total_delta_ms(self) -> float:
        return float(self.delta_ms[-1])


def compare_laps(lap_a: LapTrace, lap_b: LapTrace) -> Comparison:
    grid = common_grid(lap_a, lap_b)
    a = resample_to_grid(lap_a, grid)
    b = resample_to_grid(lap_b, grid)
    # 各自歸零到網格起點，delta 才是「這段路程內」的時間差
    t_a = a["t_ms"] - a["t_ms"][0]
    t_b = b["t_ms"] - b["t_ms"][0]
    comp = Comparison(lap_a=lap_a, lap_b=lap_b, grid=grid, a=a, b=b,
                      delta_ms=t_b - t_a)
    comp.zones = _find_brake_zones(comp)
    return comp


def _find_brake_zones(c: Comparison) -> list:
    """以「任一圈在煞車」為條件切出煞車區段，並比較兩圈行為。"""
    braking = (c.a["brake"] > BRAKE_THRESHOLD) | (c.b["brake"] > BRAKE_THRESHOLD)
    if not braking.any():
        return []

    # 找連續 True 區段
    padded = np.concatenate([[False], braking, [False]])
    starts = np.where(~padded[:-1] & padded[1:])[0]
    ends = np.where(padded[:-1] & ~padded[1:])[0]  # exclusive

    # 合併間距太近的區段
    merged = [[starts[0], ends[0]]]
    gap_pts = int(ZONE_MERGE_GAP / (c.grid[1] - c.grid[0]))
    for s, e in zip(starts[1:], ends[1:]):
        if s - merged[-1][1] <= gap_pts:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    zones = []
    n = len(c.grid)
    # 損失歸因到「下一個煞車點之前」：出彎速度差會在後面整條直線持續累積，
    # 這段損失應算在造成它的彎，而不是憑空消失
    next_starts = [m[0] for m in merged[1:]] + [n - 1]
    for idx, ((s, e), next_s) in enumerate(zip(merged, next_starts), start=1):
        # 出口往後看一小段（出彎加速區）
        exit_i = min(e + gap_pts, n - 1)
        sl = slice(s, exit_i + 1)

        def brake_on(ch):
            on = np.where(ch[sl] > BRAKE_THRESHOLD)[0]
            return float(c.grid[s + on[0]]) if len(on) else float("nan")

        # 相位拆解：以參考圈 A 的最低速點為 apex，
        # 進彎損失 = 區段起點→apex，出彎損失 = apex→下一煞車點
        apex_i = s + int(np.argmin(c.a["speed"][s:e]))

        zones.append(BrakeZone(
            index=idx,
            start=float(c.grid[s]),
            end=float(c.grid[exit_i]),
            brake_on_a=brake_on(c.a["brake"]),
            brake_on_b=brake_on(c.b["brake"]),
            min_speed_a=float(c.a["speed"][sl].min()),
            min_speed_b=float(c.b["speed"][sl].min()),
            exit_speed_a=float(c.a["speed"][exit_i]),
            exit_speed_b=float(c.b["speed"][exit_i]),
            time_lost_ms=float(c.delta_ms[next_s] - c.delta_ms[s]),
            apex=float(c.grid[apex_i]),
            entry_loss_ms=float(c.delta_ms[apex_i] - c.delta_ms[s]),
            exit_loss_ms=float(c.delta_ms[next_s] - c.delta_ms[apex_i]),
        ))
    return zones


def microsectors(c: Comparison, n: int = 25) -> list:
    """把比較範圍等分為 n 個微分段，回傳每段的時間增減（正 = B 損失）。"""
    edges = np.linspace(0, len(c.grid) - 1, n + 1).astype(int)
    return [{
        "start": float(c.grid[i0]),
        "end": float(c.grid[i1]),
        "delta_ms": float(c.delta_ms[i1] - c.delta_ms[i0]),
    } for i0, i1 in zip(edges[:-1], edges[1:])]


def tyre_summary(comp_side: dict) -> dict | None:
    """該圈的平均胎心溫度與胎壓（v2 資料才有，舊圈回傳 None）。"""
    temps = [comp_side.get(f"tyre_temp_{w}") for w in ("fl", "fr", "rl", "rr")]
    press = [comp_side.get(f"tyre_press_{w}") for w in ("fl", "fr", "rl", "rr")]
    if any(t is None for t in temps):
        return None
    return {
        "temp": [round(float(np.nanmean(t)), 1) for t in temps],
        "pressure": [round(float(np.nanmean(p)), 2) for p in press],
    }


def summarize(c: Comparison, corner_names: dict | None = None) -> str:
    """把比較結果轉成結構化文字（人可讀，也是階段四餵給 Claude 的 context）。

    corner_names: {zone_index: '彎名 (編號)'}，有對照表時區段會以彎名呈現。
    """
    corner_names = corner_names or {}
    lines = [
        f"參考圈 A：{c.lap_a.label}（{'有效' if c.lap_a.is_valid else '無效'}）",
        f"比較圈 B：{c.lap_b.label}（{'有效' if c.lap_b.is_valid else '無效'}）",
        f"整段比較範圍內 B 共慢 {c.total_delta_ms/1000:+.3f} 秒",
        "",
        f"共偵測到 {len(c.zones)} 個煞車區段（依損失排序）：",
    ]
    for z in sorted(c.zones, key=lambda z: -abs(z.time_lost_ms)):
        label = corner_names.get(z.index, f"煞車區段 #{z.index}")
        detail = [f"{label}（賽道位置 {z.start*100:.1f}%–{z.end*100:.1f}%）："
                  f"B 損失 {z.time_lost_ms/1000:+.3f} 秒"]
        if not np.isnan(z.brake_on_a) and not np.isnan(z.brake_on_b):
            diff = (z.brake_on_b - z.brake_on_a) * 100
            if abs(diff) >= 0.05:
                detail.append(f"  煞車點：B 比 A {'早' if diff < 0 else '晚'} "
                              f"{abs(diff):.2f}% 賽道距離"
                              f"（A @ {z.brake_on_a*100:.2f}%，B @ {z.brake_on_b*100:.2f}%）")
        detail.append(f"  損失拆解：煞車進彎 {z.entry_loss_ms/1000:+.3f} 秒 / "
                      f"出彎加速（含後段直線） {z.exit_loss_ms/1000:+.3f} 秒")
        detail.append(f"  彎中最低速：A {z.min_speed_a:.0f} / B {z.min_speed_b:.0f} km/h"
                      f"（差 {z.min_speed_b - z.min_speed_a:+.0f}）")
        detail.append(f"  出口速度：A {z.exit_speed_a:.0f} / B {z.exit_speed_b:.0f} km/h"
                      f"（差 {z.exit_speed_b - z.exit_speed_a:+.0f}）")
        lines.extend(detail)
    return "\n".join(lines)
