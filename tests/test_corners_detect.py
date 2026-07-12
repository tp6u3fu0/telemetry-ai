"""彎道自動偵測測試：橫向 G 濾雜訊、chicane 拆兩彎、依序編號、方向正確。

執行：uv run python tests/test_corners_detect.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np                                    # noqa: E402

from analysis.corners_detect import detect_corners    # noqa: E402


def build_lap():
    """合成一圈：4 個真彎（含一個 chicane）+ 1 個直線方向盤微修（應被濾掉）。"""
    grid = np.linspace(0.0, 1.0, 1000)
    steer = np.zeros(1000)
    lat = np.zeros(1000)
    speed = np.full(1000, 250.0)
    gear = np.full(1000, 6.0)

    def seg(a, b, st, la, spd, gr):
        m = (grid >= a) & (grid < b)
        steer[m], lat[m], speed[m], gear[m] = st, la, spd, gr

    seg(0.10, 0.125, -0.4, -0.8, 90, 2)    # T1 左（G 用真實量級 ~0.8g）
    seg(0.125, 0.15, 0.4, 0.8, 100, 3)     # T2 右（與 T1 相接 → chicane，靠轉向反轉拆開）
    seg(0.40, 0.50, 0.5, 0.9, 150, 4)      # T3 右（長彎 → 一個）
    seg(0.60, 0.615, 0.2, 0.05, 240, 6)    # 直線微修：方向盤有動但橫向 G 極小 → 應濾掉
    seg(0.75, 0.78, -0.3, -0.7, 120, 3)    # T4 左
    return grid, {"steering": steer, "acc_lat": lat, "speed": speed, "gear": gear}


def main() -> int:
    failures = []
    grid, ch = build_lap()
    corners = detect_corners(grid, ch)

    if len(corners) != 4:
        failures.append(f"應偵測到 4 個彎（微修被濾掉），得 {len(corners)}："
                        f"{[(c.label, round(c.start, 3), c.direction) for c in corners]}")

    if corners:
        labels = [c.label for c in corners]
        if labels != ["T1", "T2", "T3", "T4"]:
            failures.append(f"編號應依序 T1..T4，得 {labels}")
        dirs = [c.direction for c in corners]
        if dirs != ["left", "right", "right", "left"]:
            failures.append(f"方向錯：{dirs}")
        # chicane：T1、T2 應緊鄰且方向相反
        if len(corners) >= 2 and not (corners[0].direction == "left"
                                      and corners[1].direction == "right"):
            failures.append("chicane 應拆成左(T1)+右(T2)")
        # apex 最低速抓對（T3 長右彎 min≈150）
        t3 = next((c for c in corners if c.label == "T3"), None)
        if t3 and abs(t3.min_speed - 150) > 5:
            failures.append(f"T3 最低速錯：{t3.min_speed}")
        if t3 and t3.gear != 4:
            failures.append(f"T3 apex 檔位錯：{t3.gear}")
        # 直線微修不應成為彎（無彎落在 0.60 附近）
        if any(0.59 < c.start < 0.62 for c in corners):
            failures.append("直線方向盤微修不應被當成彎")

    # -- 無橫向 G（舊資料）→ 退回純方向盤：微修會被算進來（可接受的退化） --
    ch2 = {k: v for k, v in ch.items() if k != "acc_lat"}
    corners2 = detect_corners(grid, ch2)
    if len(corners2) < 4:
        failures.append(f"純方向盤退化模式至少應抓到 4 真彎，得 {len(corners2)}")

    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        return 1
    print(f"PASS  (橫向 G 濾雜訊 + chicane 拆彎 + 依序編號 + 方向；偵測 {len(corners)} 彎)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
