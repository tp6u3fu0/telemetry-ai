"""從遙測自動偵測「所有彎」並依 spline 順序編號（T1、T2…）。

與 single.py 的煞車區段不同——這裡偵測的是「轉向」而非「煞車」，
所以全油門通過的高速彎也算得到，號碼才對得上官方編號。

判定：
- 主偵測訊號優先用橫向 G（acc_lat，v2 通道）——它才是乾淨的「是否在過彎」
  訊號；沒有時退回方向盤（但方向盤是用最大轉角正規化的，數值偏小，門檻另設）
- 訊號先平滑，避免路面顛簸/雜訊把一個彎切成好幾段
- 方向（左/右）一律取自方向盤正負（-1 左、+1 右，已文件化）；在訊號正負
  反轉處切開，所以 chicane（左-右）自動分成兩個彎、長彎維持一個
- 同方向但中間隔了一段直線（gap 過大）也切成兩個彎

純邏輯、吃 resample 後的網格通道，可離線測試。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LAT_G_ON = 0.45         # 橫向 G 門檻（g）——主偵測訊號
STEER_ON = 0.05         # 方向盤門檻（無 acc_lat 時的退化訊號；數值偏小故門檻低）
SMOOTH_PCT = 0.006      # 偵測訊號平滑窗（賽道比例）
MERGE_GAP = 0.012       # 同方向兩段間隔 < 此值（spline 比例）視為同一彎
MIN_LEN = 0.005         # 短於此的段視為雜訊丟棄（約賽道 0.5%）


def _smooth(x: np.ndarray, w: int) -> np.ndarray:
    if w <= 1:
        return x
    return np.convolve(x, np.ones(w) / w, mode="same")


@dataclass
class Corner:
    index: int             # 1-based 彎號 → label "T{index}"
    start: float           # 進彎 spline
    end: float             # 出彎 spline
    apex: float            # 最低速位置 spline
    direction: str         # "left" / "right"
    entry_speed: float
    min_speed: float
    exit_speed: float
    gear: int              # apex 檔位

    @property
    def label(self) -> str:
        return f"T{self.index}"


def detect_corners(grid: np.ndarray, ch: dict) -> list:
    """grid: spline 網格；ch: resample 後的通道（需 steering，acc_lat 選用）。"""
    steer = ch.get("steering")
    if steer is None or len(grid) < 2:
        return []
    n = len(grid)
    dstep = grid[1] - grid[0]
    w = max(1, int(SMOOTH_PCT / dstep))

    # 主偵測訊號：優先橫向 G（乾淨），否則退回方向盤
    lat = ch.get("acc_lat")
    if lat is not None and not np.all(np.isnan(lat)):
        sig, thr = _smooth(lat, w), LAT_G_ON
    else:
        sig, thr = _smooth(steer, w), STEER_ON
    # 方向恆取自方向盤正負（已文件化 -1 左 +1 右）；訊號用來偵測與切彎
    steer_s = _smooth(steer, w)
    direction = np.where(np.abs(sig) > thr, np.sign(sig), 0).astype(int)

    # 依方向切段：方向反轉、或同方向但隔太遠 → 新的一段
    segs = []                                  # [start_i, end_i, dir]
    cur = None
    last_on = None
    max_gap_pts = int(MERGE_GAP / dstep)
    for i in range(n):
        d = direction[i]
        if d == 0:
            continue
        if cur is None:
            cur = [i, i, d]
        elif d == cur[2] and (i - last_on) <= max_gap_pts:
            cur[1] = i                         # 同方向、間隔小 → 延伸
        else:
            segs.append(cur)                   # 反轉或隔太遠 → 收尾開新
            cur = [i, i, d]
        last_on = i
    if cur is not None:
        segs.append(cur)

    corners = []
    for s, e in ((seg[0], seg[1]) for seg in segs):
        if grid[e] - grid[s] < MIN_LEN:        # 太短 = 雜訊
            continue
        sl = slice(s, e + 1)
        apex_i = s + int(np.argmin(ch["speed"][sl]))
        gear = ch.get("gear")
        corners.append(Corner(
            index=0,                           # 稍後統一編號
            start=float(grid[s]), end=float(grid[e]), apex=float(grid[apex_i]),
            direction="right" if steer_s[apex_i] > 0 else "left",
            entry_speed=float(ch["speed"][s]),
            min_speed=float(ch["speed"][sl].min()),
            exit_speed=float(ch["speed"][e]),
            gear=int(round(gear[apex_i])) if gear is not None else 0,
        ))

    for i, c in enumerate(corners, start=1):   # 依 spline 順序編號 T1..TN
        c.index = i
    return corners
