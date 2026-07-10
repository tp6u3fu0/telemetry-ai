"""單圈分析：不需要參考圈，看一圈自己的煞車區段與速度特性。

與 compare.py 的差異：沒有 delta / 損失概念，區段指標是絕對值
（煞車點、彎中最低速、出口速度），供單圈檢視與 AI 教練用。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .loader import GRID_N, LapTrace, resample_to_grid

BRAKE_THRESHOLD = 0.10
ZONE_MERGE_GAP = 0.01


@dataclass
class SingleZone:
    index: int
    start: float
    end: float
    apex: float            # 最低速位置
    brake_on: float        # 開始煞車的 spline
    min_speed: float
    exit_speed: float
    entry_speed: float     # 煞車前速度（煞車點的瞬時速度）


@dataclass
class SingleAnalysis:
    trace: LapTrace
    grid: np.ndarray
    ch: dict               # resample 後各通道
    zones: list

    @property
    def top_speed(self) -> float:
        return float(self.ch["speed"].max())

    @property
    def min_speed(self) -> float:
        return float(self.ch["speed"].min())


def analyze_lap(trace: LapTrace) -> SingleAnalysis:
    lo = max(0.0, float(trace.spline[0]))
    hi = min(1.0, float(trace.spline[-1]))
    grid = np.linspace(lo, hi, GRID_N)
    ch = resample_to_grid(trace, grid)
    return SingleAnalysis(trace=trace, grid=grid, ch=ch,
                          zones=_brake_zones(grid, ch))


def _brake_zones(grid: np.ndarray, ch: dict) -> list:
    braking = ch["brake"] > BRAKE_THRESHOLD
    if not braking.any():
        return []
    padded = np.concatenate([[False], braking, [False]])
    starts = np.where(~padded[:-1] & padded[1:])[0]
    ends = np.where(padded[:-1] & ~padded[1:])[0]

    gap_pts = int(ZONE_MERGE_GAP / (grid[1] - grid[0]))
    merged = [[starts[0], ends[0]]]
    for s, e in zip(starts[1:], ends[1:]):
        if s - merged[-1][1] <= gap_pts:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    n = len(grid)
    zones = []
    for idx, (s, e) in enumerate(merged, start=1):
        exit_i = min(e + gap_pts, n - 1)
        sl = slice(s, exit_i + 1)
        apex_i = s + int(np.argmin(ch["speed"][sl]))
        zones.append(SingleZone(
            index=idx,
            start=float(grid[s]),
            end=float(grid[exit_i]),
            apex=float(grid[apex_i]),
            brake_on=float(grid[s]),
            min_speed=float(ch["speed"][sl].min()),
            exit_speed=float(ch["speed"][exit_i]),
            entry_speed=float(ch["speed"][s]),
        ))
    return zones


def summarize_single(a: SingleAnalysis, corner_names: dict | None = None) -> str:
    """單圈文字摘要（AI 教練 context 用）。"""
    corner_names = corner_names or {}
    t = a.trace
    lines = [
        f"單圈分析：{t.label}（{'有效' if t.is_valid else '無效'}圈"
        f"{'' if t.is_complete else '，不完整'}）",
        f"極速 {a.top_speed:.0f} km/h，全圈最低速 {a.min_speed:.0f} km/h",
        "",
        f"共 {len(a.zones)} 個煞車區段（依賽道順序）：",
    ]
    for z in a.zones:
        label = corner_names.get(z.index, f"煞車區段 #{z.index}")
        lines.append(
            f"{label}：於 {z.brake_on*100:.1f}% 開始煞車（當時 {z.entry_speed:.0f} km/h），"
            f"彎中最低速 {z.min_speed:.0f} km/h @ {z.apex*100:.1f}%，"
            f"出口速度 {z.exit_speed:.0f} km/h")
    lines.append("")
    lines.append("注意：這是單圈資料，沒有參考圈可比較。請根據賽道知識與"
                 "各彎的絕對數據（煞車點、彎中速度、出口速度）給出改進方向，"
                 "並明確說明哪些判斷需要更多圈數驗證。")
    return "\n".join(lines)
