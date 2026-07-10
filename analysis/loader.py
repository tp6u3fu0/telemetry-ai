"""把 DB 裡的一圈逐點資料載入成 numpy 陣列，並重取樣到統一的 spline 網格。

兩圈要能逐點比較，必須先對齊到同一個「賽道位置」座標——
以 spline position (0~1) 建立統一網格，把每圈的時間/速度/油門/煞車
內插到網格上，之後所有 delta 分析都在網格上進行。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from data_store.db import TelemetryDB

GRID_N = 1000  # spline 網格解析度（每 0.1% 賽道一點）

# v2 通道（舊資料為 NULL → nan；載入後以 has_channel() 檢查可用性）
V2_CHANNELS = ["world_x", "world_y", "acc_lat", "acc_lon",
               "tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr",
               "tyre_press_fl", "tyre_press_fr", "tyre_press_rl", "tyre_press_rr"]


@dataclass
class LapTrace:
    lap_id: int
    session_id: int
    lap_number: int
    lap_time_ms: int
    is_valid: bool
    is_complete: bool
    t_ms: np.ndarray
    spline: np.ndarray
    speed: np.ndarray
    throttle: np.ndarray
    brake: np.ndarray
    steering: np.ndarray
    gear: np.ndarray
    rpm: np.ndarray
    extra: dict            # v2 通道 name -> ndarray（可能全為 nan）

    def has_channel(self, name: str) -> bool:
        arr = self.extra.get(name)
        return arr is not None and not np.all(np.isnan(arr))

    @property
    def label(self) -> str:
        from telemetry_listener.live_console import format_laptime
        return f"Lap {self.lap_number} ({format_laptime(self.lap_time_ms)})"


def load_lap(db: TelemetryDB, lap_id: int) -> LapTrace:
    lap = db.get_lap(lap_id)
    if lap is None:
        raise ValueError(f"lap_id {lap_id} 不存在")
    rows = db.get_lap_points(lap_id)
    if not rows:
        raise ValueError(f"lap_id {lap_id} 沒有遙測點")

    def col(name):
        return np.array([r[name] for r in rows], dtype=float)  # None -> nan

    cols = {k: col(k) for k in ("t_ms", "spline", "speed_kmh", "throttle",
                                "brake", "steering", "gear", "rpm")}
    row_keys = rows[0].keys()
    extra = {k: col(k) for k in V2_CHANNELS if k in row_keys}

    # 清理 spline：
    # 1) 真過線（>0.7 掉到 <0.3）→ 解捲 +1
    # 2) 其他大跳（|Δ| > 0.05，正常取樣每步僅 ~0.0005）→ 撕裂讀取的雜訊點，丟棄
    # 3) 最後平移讓圈的主體落在 [0,1)（處理圈首殘留的過線前樣本）
    raw = cols["spline"]
    spline = raw.copy()
    keep = np.ones(len(raw), dtype=bool)
    offset = 0.0
    last = raw[0]
    for i in range(1, len(raw)):
        d = raw[i] - last
        if d < -0.5 and last > 0.7 and raw[i] < 0.3:
            offset += 1.0            # 真正跨越終點線
            last = raw[i]
        elif abs(d) > 0.05:
            keep[i] = False          # 雜訊，不更新基準
            continue
        else:
            last = raw[i]
        spline[i] = raw[i] + offset
    spline -= np.floor(np.median(spline[keep]))

    # 仍須嚴格遞增才能內插（剔除殘餘抖動點）
    running_max = np.maximum.accumulate(np.where(keep, spline, -np.inf))
    keep[1:] &= spline[1:] > running_max[:-1]

    return LapTrace(
        lap_id=lap_id,
        session_id=lap["session_id"],
        lap_number=lap["lap_number"],
        lap_time_ms=lap["lap_time_ms"],
        is_valid=bool(lap["is_valid"]),
        is_complete=bool(lap["is_complete"]),
        t_ms=cols["t_ms"][keep],
        spline=spline[keep],
        speed=cols["speed_kmh"][keep],
        throttle=cols["throttle"][keep],
        brake=cols["brake"][keep],
        steering=cols["steering"][keep],
        gear=cols["gear"][keep],
        rpm=cols["rpm"][keep],
        extra={k: v[keep] for k, v in extra.items()},
    )


def resample_to_grid(trace: LapTrace, grid: np.ndarray) -> dict:
    """把一圈的各通道內插到 spline 網格上。網格點必須落在該圈資料範圍內。"""
    out = {}
    for name in ("t_ms", "speed", "throttle", "brake", "steering", "rpm"):
        out[name] = np.interp(grid, trace.spline, getattr(trace, name))
    out["gear"] = np.round(np.interp(grid, trace.spline, trace.gear))
    for name, arr in trace.extra.items():
        if trace.has_channel(name):
            out[name] = np.interp(grid, trace.spline, arr)
    return out


def common_grid(a: LapTrace, b: LapTrace, n: int = GRID_N) -> np.ndarray:
    """兩圈 spline 範圍的交集網格（處理 partial lap 只有部分賽道的情況）。

    截止在 [0, 1]：圈界偵測延遲會讓圈尾帶到下一圈的樣本（解捲後 spline > 1），
    那段不屬於本圈，納入會汙染 delta（例如最後一圈過線後收油滑行）。
    """
    lo = max(a.spline[0], b.spline[0], 0.0)
    hi = min(a.spline[-1], b.spline[-1], 1.0)
    if hi - lo < 0.05:
        raise ValueError(f"兩圈的賽道範圍幾乎沒有重疊（{lo:.3f} ~ {hi:.3f}）")
    return np.linspace(lo, hi, n)
