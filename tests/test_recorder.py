"""離線測試階段二：用合成的三圈資料驅動 LapRecorder，驗證 SQLite 內容。

執行：python tests/test_recorder.py
模擬情境：起錄時已在圈中（partial）→ 跑完 3 圈（第 2 圈切西瓜 invalid）→ Ctrl+C。
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_store.db import TelemetryDB                       # noqa: E402
from data_store.recorder import LapRecorder                 # noqa: E402
from telemetry_listener.shared_memory import (              # noqa: E402
    GraphicsSnapshot, PhysicsSnapshot)

LAP_MS = 90_000
DT = 20  # 50Hz


def make_sample(packet_id, completed, t_in_lap, last_lap_ms, valid):
    spline = t_in_lap / LAP_MS
    phys = PhysicsSnapshot(
        packet_id=packet_id,
        throttle=max(0.0, math.sin(spline * 6 * math.pi)),
        brake=max(0.0, -math.sin(spline * 6 * math.pi)),
        gear=3,
        rpm=6000 + int(2000 * spline),
        steer_angle=0.1,
        speed_kmh=150 + 80 * math.sin(spline * 2 * math.pi) ** 2,
        acc_lat=math.sin(spline * 4 * math.pi) * 2.0,
        acc_lon=-1.0,
        tyre_temp=(82.0, 83.0, 80.0, 81.0),
        tyre_pressure=(27.5, 27.6, 27.2, 27.3),
    )
    gfx = GraphicsSnapshot(
        packet_id=packet_id, status=2, session_type=0,
        completed_laps=completed,
        current_lap_time_ms=t_in_lap,
        last_lap_time_ms=last_lap_ms,
        best_lap_time_ms=last_lap_ms,
        spline_position=spline,
        is_valid_lap=valid,
        is_in_pit=False, current_sector=int(spline * 3),
        world_x=math.cos(spline * 2 * math.pi) * 500,
        world_y=math.sin(spline * 2 * math.pi) * 300,
    )
    return phys, gfx


def main() -> int:
    db_path = os.path.join(tempfile.mkdtemp(), "test.sqlite3")
    db = TelemetryDB(db_path)
    session_id = db.create_session("monza", "ferrari_296_gt3", "Test Driver", 0)
    rec = LapRecorder(db, session_id)

    packet = 0
    lap_times = {1: 91_234, 2: 92_500, 3: 90_111}
    # 從第 0 圈的一半開始錄（partial lap）
    for completed in range(4):  # 圈 0(partial), 1, 2, 3
        start = LAP_MS // 2 if completed == 0 else 0
        for t in range(start, LAP_MS, DT):
            packet += 1
            # 第 2 圈（completed==2 進行中）在 30% 處切西瓜 → invalid
            valid = not (completed == 2 and t >= LAP_MS * 0.3)
            last = lap_times.get(completed)  # 上一圈的圈速
            phys, gfx = make_sample(packet, completed, t, last or 0, valid)
            rec.process_sample(phys, gfx)
        if completed < 3:
            # 圈界：completedLaps +1、iCurrentTime 歸零、iLastTime 更新
            packet += 1
            phys, gfx = make_sample(packet, completed + 1, 0,
                                    lap_times[completed + 1], True)
            rec.process_sample(phys, gfx)
    rec.finalize()  # 第 4 圈跑到一半 Ctrl+C

    failures = []
    laps = db.list_laps(session_id)

    if len(laps) != 4:
        failures.append(f"expected 4 laps, got {len(laps)}")
    else:
        l1, l2, l3, l4 = laps
        # 圈1：partial（起錄時已在圈中）→ incomplete，圈速仍記錄
        if l1["is_complete"] or l1["lap_time_ms"] != 91_234:
            failures.append(f"lap1 wrong: complete={l1['is_complete']} t={l1['lap_time_ms']}")
        # 圈2：完整有效
        if not (l2["is_complete"] and l2["is_valid"] and l2["lap_time_ms"] == 92_500):
            failures.append(f"lap2 wrong: {dict(l2)}")
        # 圈3：切西瓜 → invalid
        if not l3["is_complete"] or l3["is_valid"]:
            failures.append(f"lap3 should be complete+invalid: {dict(l3)}")
        if l3["lap_time_ms"] != 90_111:
            failures.append(f"lap3 time wrong: {l3['lap_time_ms']}")
        # 圈4：Ctrl+C 中斷 → incomplete，無圈速
        if l4["is_complete"] or l4["lap_time_ms"] is not None:
            failures.append(f"lap4 wrong: {dict(l4)}")
        # 完整圈點數 ≈ LAP_MS/DT
        expected = LAP_MS // DT
        if abs(l2["point_count"] - expected) > 5:
            failures.append(f"lap2 point count {l2['point_count']} != ~{expected}")
        # 逐點資料可撈回、按時間排序、spline 遞增
        pts = db.get_lap_points(l2["lap_id"])
        if len(pts) != l2["point_count"]:
            failures.append("point_count mismatch with actual rows")
        t_list = [p["t_ms"] for p in pts]
        s_list = [p["spline"] for p in pts]
        if t_list != sorted(t_list) or s_list != sorted(s_list):
            failures.append("points not ordered by time/spline")
        if not all(0.0 <= p["throttle"] <= 1.0 and 0.0 <= p["brake"] <= 1.0 for p in pts):
            failures.append("throttle/brake out of range")
        # best_lap 應選圈2（有效完整圈中：91234 是 incomplete、90111 invalid）
        best = db.best_lap(session_id)
        if best is None or best["lap_id"] != l2["lap_id"]:
            failures.append(f"best lap wrong: {dict(best) if best else None}")

    db.close()
    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        return 1
    total_pts = sum(l["point_count"] for l in laps)
    print(f"PASS  (4 laps, {total_pts} points, validity/completeness/best-lap all correct)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
