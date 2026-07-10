"""離線測試階段三：合成兩圈（B 在第 2 彎煞車較早、出彎較慢），驗證分析結果。

執行：python tests/test_analysis.py
會在 data/ 輸出 test_compare.png 供目視檢查。
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np                                   # noqa: E402

from analysis.compare import (                        # noqa: E402
    compare_laps, microsectors, summarize, tyre_summary)
from analysis.loader import load_lap                  # noqa: E402
from analysis.plot import plot_comparison             # noqa: E402
from data_store.db import TelemetryDB                 # noqa: E402

TRACK_M = 4000.0
VMAX = 250.0
# 三個彎：(減速起點, apex, 出彎完成點, 彎中最低速)
CORNERS_A = [(0.15, 0.20, 0.26, 120.0),
             (0.45, 0.50, 0.57, 80.0),
             (0.75, 0.80, 0.86, 150.0)]
# B：第 2 彎提早 1.5% 賽道距離開始煞車、彎中低 6 km/h、出彎晚 1% 才回到全速
CORNERS_B = [(0.15, 0.20, 0.26, 120.0),
             (0.435, 0.50, 0.58, 74.0),
             (0.75, 0.80, 0.86, 150.0)]


def speed_profile(s: np.ndarray, corners) -> np.ndarray:
    v = np.full_like(s, VMAX)
    for entry, apex, exit_, vmin in corners:
        dec = (s >= entry) & (s < apex)
        v[dec] = np.minimum(v[dec], VMAX + (vmin - VMAX) * (s[dec] - entry) / (apex - entry))
        acc = (s >= apex) & (s < exit_)
        v[acc] = np.minimum(v[acc], vmin + (VMAX - vmin) * (s[acc] - apex) / (exit_ - apex))
    return v


def synth_lap(corners, dt_ms=20):
    """由速度剖面積分出 t(s)，再以固定取樣週期產生逐點資料。"""
    s_fine = np.linspace(0, 1, 20000)
    v = speed_profile(s_fine, corners)            # km/h
    v_ms = v / 3.6
    ds_m = np.diff(s_fine) * TRACK_M
    t_s = np.concatenate([[0], np.cumsum(ds_m / v_ms[:-1])])
    lap_time_ms = int(t_s[-1] * 1000)

    t_samples = np.arange(0, t_s[-1], dt_ms / 1000.0)
    s_samples = np.interp(t_samples, t_s, s_fine)
    v_samples = np.interp(s_samples, s_fine, v)
    dv = np.gradient(v_samples)
    brake = np.clip(-dv * 2.0, 0, 1)
    throttle = np.where(brake > 0.02, 0.0, np.clip(0.4 + dv * 2.0, 0, 1))

    # v2 通道：圓形賽道座標 + 固定胎溫/胎壓（驗證 loader/API 傳遞）
    points = [(int(t * 1000), float(s), float(vv), float(th), float(br),
               0.0, 4, 7000,
               math.cos(2 * math.pi * s) * 600, math.sin(2 * math.pi * s) * 400,
               1.5, -0.5, 82.0, 83.0, 80.0, 81.0, 27.5, 27.6, 27.2, 27.3)
              for t, s, vv, th, br in zip(t_samples, s_samples, v_samples,
                                          throttle, brake)]
    # 模擬真實資料的過線殘留：圈首有一點還在線前（spline≈0.999）、
    # 圈尾有一點已繞回 0.00x——loader 必須能解捲而不是把整圈丟掉
    tail = (1.5, -0.5, 82.0, 83.0, 80.0, 81.0, 27.5, 27.6, 27.2, 27.3)
    points.insert(0, (0, 0.9993, VMAX, 1.0, 0.0, 0.0, 4, 7000, 600.0, -2.0, *tail))
    points.append((points[-1][0] + dt_ms, 0.0004, VMAX, 1.0, 0.0, 0.0, 4, 7000,
                   600.0, 2.0, *tail))
    # 模擬撕裂讀取：圈中插入兩個 spline 亂跳的雜訊點（iRacing 實測出現過），
    # loader 必須丟棄而不是當成過線解捲
    mid = len(points) // 2
    t_mid = points[mid][0]
    points.insert(mid, (t_mid, 0.02, VMAX, 1.0, 0.0, 0.0, 4, 7000, 0.0, 0.0, *tail))
    points.insert(mid, (t_mid - dt_ms, 0.97, VMAX, 1.0, 0.0, 0.0, 4, 7000, 0.0, 0.0, *tail))
    return lap_time_ms, points


def main() -> int:
    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "test_analysis.sqlite3")
    if os.path.exists(db_path):
        os.remove(db_path)
    db = TelemetryDB(db_path)
    session_id = db.create_session("synth_track", "test_car", "Tester", 0)

    time_a, pts_a = synth_lap(CORNERS_A)
    time_b, pts_b = synth_lap(CORNERS_B)
    id_a = db.save_lap(session_id, 1, time_a, True, True, pts_a)
    id_b = db.save_lap(session_id, 2, time_b, True, True, pts_b)

    comp = compare_laps(load_lap(db, id_a), load_lap(db, id_b))
    text = summarize(comp)
    out_png = os.path.join(os.path.dirname(db_path), "test_compare.png")
    plot_comparison(comp, out_png)

    failures = []
    expected_diff = time_b - time_a
    if abs(comp.total_delta_ms - expected_diff) > 150:
        failures.append(f"total delta {comp.total_delta_ms:.0f}ms != 圈速差 {expected_diff}ms")
    if len(comp.zones) != 3:
        failures.append(f"expected 3 brake zones, got {len(comp.zones)}")
    else:
        worst = max(comp.zones, key=lambda z: z.time_lost_ms)
        if not (0.40 < worst.start < 0.50):
            failures.append(f"worst zone at {worst.start:.2f}, expected corner 2 (~0.44)")
        if worst.brake_on_b >= worst.brake_on_a:
            failures.append("B 應該比 A 早煞車，偵測相反")
        if worst.min_speed_b >= worst.min_speed_a:
            failures.append("B 彎中最低速應較低")
        # 另外兩彎兩圈相同 → 損失應接近 0
        for z in comp.zones:
            if z is not worst and abs(z.time_lost_ms) > 100:
                failures.append(f"zone #{z.index} 應無損失，得 {z.time_lost_ms:.0f}ms")
    if "煞車區段" not in text or "出口速度" not in text or "損失拆解" not in text:
        failures.append("summary 缺少關鍵欄位")

    # 微分段：各段 delta 總和應等於整體 delta
    ms = microsectors(comp)
    ms_sum = sum(m["delta_ms"] for m in ms)
    if abs(ms_sum - comp.total_delta_ms) > 1:
        failures.append(f"microsector 總和 {ms_sum:.0f} != {comp.total_delta_ms:.0f}")
    # 相位拆解：進彎 + 出彎 = 區段總損失
    for z in comp.zones:
        if abs(z.entry_loss_ms + z.exit_loss_ms - z.time_lost_ms) > 1:
            failures.append(f"zone #{z.index} 相位拆解不守恆")
    # 第 2 彎的性質：B 提早煞車 → 進彎就開始損失；出彎慢 → 出彎段損失更大
    worst2 = max(comp.zones, key=lambda z: z.time_lost_ms)
    if not (worst2.entry_loss_ms > 50 and worst2.exit_loss_ms > 100):
        failures.append(f"相位拆解不合理: entry={worst2.entry_loss_ms:.0f} "
                        f"exit={worst2.exit_loss_ms:.0f}")
    # v2 通道：地圖座標與胎溫摘要
    trace = load_lap(db, id_a)
    if not trace.has_channel("world_x"):
        failures.append("world_x 通道遺失")
    ty = tyre_summary(comp.a)
    if ty is None or abs(ty["temp"][0] - 82.0) > 0.5 or abs(ty["pressure"][3] - 27.3) > 0.05:
        failures.append(f"tyre_summary 錯誤: {ty}")
    if not os.path.exists(out_png) or os.path.getsize(out_png) < 20_000:
        failures.append("PNG 輸出異常")

    db.close()
    print(text)
    print()
    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        return 1
    print(f"PASS  (delta 收斂於圈速差、第 2 彎正確被抓出為最大損失，圖已輸出 {out_png})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
