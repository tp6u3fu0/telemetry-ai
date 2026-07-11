"""F1 25 adapter 測試：合成官方格式封包 → 解析 → snapshot → 完整錄圈。

執行：uv run python tests/test_f1.py
"""
import os
import socket
import struct
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_store.db import TelemetryDB          # noqa: E402
from data_store.recorder import LapRecorder    # noqa: E402
from sources.f1_25 import F1Reader             # noqa: E402

PLAYER = 3   # 玩家不在第 0 格，驗證 index 位移


def header(packet_id, frame):
    return struct.pack("<HBBBBBQfIIBB", 2025, 25, 1, 0, 1, packet_id,
                       12345, 100.0, frame, frame, PLAYER, 255)


def telemetry_packet(frame, speed, throttle, brake, gear, rpm, steer=0.1):
    car = struct.pack("<HfffBbHBBH", speed, throttle, steer, brake, 0, gear, rpm,
                      0, 50, 0)
    car += struct.pack("<4H", 400, 410, 380, 390)          # brake temps
    car += struct.pack("<4B", 90, 91, 92, 93)              # surface [RL,RR,FL,FR]
    car += struct.pack("<4B", 100, 101, 102, 103)          # inner   [RL,RR,FL,FR]
    car += struct.pack("<H", 105)                          # engine temp
    car += struct.pack("<4f", 21.0, 21.5, 22.0, 22.5)      # pressure [RL,RR,FL,FR]
    car += struct.pack("<4B", 0, 0, 0, 0)                  # surface type
    assert len(car) == 60, len(car)
    cars = b"\x00" * 60 * PLAYER + car + b"\x00" * 60 * (22 - PLAYER - 1)
    return header(6, frame) + cars + b"\x00\x00\x00"


def lap_packet(frame, last_ms, cur_ms, lap_dist, lap_num, invalid=0,
               driver_status=1, pit=0):
    car = struct.pack("<II", last_ms, cur_ms)
    car += struct.pack("<HBHB", 30000, 0, 30000, 0)        # sector 1/2
    car += struct.pack("<HBHB", 0, 0, 0, 0)                # deltas
    car += struct.pack("<fff", lap_dist, 10000.0, 0.0)     # lapDist/total/sc
    car += struct.pack("<6B", 1, lap_num, pit, 0, 0, invalid)
    car += struct.pack("<6B", 0, 0, 0, 0, 0, 0)            # penalties..grid
    car += struct.pack("<BB", driver_status, 2)            # driverStatus/result
    car += struct.pack("<BHHB", 0, 0, 0, 0)                # pitlane timers
    car += struct.pack("<fB", 280.0, 1)                    # speed trap
    assert len(car) == 57, len(car)
    cars = b"\x00" * 57 * PLAYER + car + b"\x00" * 57 * (22 - PLAYER - 1)
    return header(2, frame) + cars + b"\x00\x00"


def motion_packet(frame, x, z, g_lat=1.2, g_lon=-0.8):
    car = struct.pack("<fff", x, 5.0, z)                   # worldPos X/Y/Z
    car += struct.pack("<fff", 0, 0, 0)                    # velocity
    car += struct.pack("<6h", 0, 0, 0, 0, 0, 0)            # direction vectors
    car += struct.pack("<fff", g_lat, g_lon, 0.5)          # G lat/lon/vert
    car += struct.pack("<fff", 0, 0, 0)                    # yaw/pitch/roll
    assert len(car) == 60, len(car)
    cars = b"\x00" * 60 * PLAYER + car + b"\x00" * 60 * (22 - PLAYER - 1)
    return header(0, frame) + cars


def session_packet(frame, track_len=5300, session_type=10, track_id=11):
    body = struct.pack("<BbbB", 0, 30, 25, 50)             # weather/temps/laps
    body += struct.pack("<HBb", track_len, session_type, track_id)
    body += b"\x00" * (753 - 29 - len(body))
    return header(1, frame) + body


def main() -> int:
    failures = []
    r = F1Reader(listen=False)

    r.parse_datagram(session_packet(1))
    r.parse_datagram(telemetry_packet(2, speed=280, throttle=0.95, brake=0.0,
                                      gear=7, rpm=11500))
    r.parse_datagram(lap_packet(3, last_ms=90500, cur_ms=45000,
                                lap_dist=2650.0, lap_num=3))
    r.parse_datagram(motion_packet(4, x=150.0, z=-80.0))

    p = r.read_physics()
    if p.speed_kmh != 280 or p.gear != 7 or p.rpm != 11500:
        failures.append(f"telemetry 解析錯: {p.speed_kmh}, {p.gear}, {p.rpm}")
    if abs(p.throttle - 0.95) > 1e-6:
        failures.append(f"油門錯: {p.throttle}")
    # 胎溫順序 [RL,RR,FL,FR] → (FL,FR,RL,RR)
    if p.tyre_temp != (102.0, 103.0, 100.0, 101.0):
        failures.append(f"胎溫順序錯: {p.tyre_temp}")
    if p.tyre_pressure != (22.0, 22.5, 21.0, 21.5):
        failures.append(f"胎壓順序錯: {p.tyre_pressure}")
    if abs(p.acc_lat - 1.2) > 1e-6 or abs(p.acc_lon + 0.8) > 1e-6:
        failures.append(f"G 值錯: {p.acc_lat}, {p.acc_lon}")

    g = r.read_graphics()
    if g.status != 2:
        failures.append(f"driver_status=1 應為 LIVE: {g.status}")
    if abs(g.spline_position - 2650 / 5300) > 1e-4:
        failures.append(f"spline 錯: {g.spline_position}")
    if g.completed_laps != 2 or g.current_lap_time_ms != 45000 \
            or g.last_lap_time_ms != 90500:
        failures.append(f"lap 資料錯: {g.completed_laps}, {g.current_lap_time_ms}")
    if g.world_x != 150.0 or g.world_y != -80.0:
        failures.append(f"世界座標錯: {g.world_x}, {g.world_y}")

    s = r.read_static()
    if s.track != "Monza" or s.sector_count != 3:
        failures.append(f"session 解析錯: {s}")
    if r.read_graphics().session_type != 2:
        failures.append("race session_type 應為 2")

    # 無效圈與 pit 旗標
    r.parse_datagram(lap_packet(5, 90500, 46000, 2700.0, 3, invalid=1, pit=1))
    g = r.read_graphics()
    if g.is_valid_lap or not g.is_in_pit:
        failures.append("invalid/pit 旗標解析錯")

    # 完整錄一圈（lapDistance 驅動 spline；過線 lap_num+1、cur 歸零）
    db = TelemetryDB(os.path.join(tempfile.mkdtemp(), "f1.sqlite3"))
    sid = db.create_session("Monza", "F1 25", "", 2, game="f1_25")
    rec = LapRecorder(db, sid)
    lap_ms, track_len, frame = 90000, 5300, 100
    for t in range(0, lap_ms, 20):
        frame += 1
        r.parse_datagram(lap_packet(frame, 0, t, track_len * t / lap_ms, 1))
        r.parse_datagram(telemetry_packet(frame, 250, 0.9, 0.0, 6, 10000))
        rec.process_sample(r.read_physics(), r.read_graphics())
    frame += 1
    r.parse_datagram(lap_packet(frame, lap_ms, 30, 5.0, 2))   # 過線
    rec.process_sample(r.read_physics(), r.read_graphics())
    rec.finalize()
    laps = db.list_laps(sid)
    if not laps or laps[0]["lap_time_ms"] != lap_ms or not laps[0]["is_valid"]:
        failures.append(f"F1 錄圈錯: {[dict(l) for l in laps]}")
    db.close()

    # 真實 UDP loopback：確認背景 thread 收包
    port = 29999
    live = F1Reader(port=port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(telemetry_packet(999, 300, 1.0, 0.0, 8, 12000), ("127.0.0.1", port))
    time.sleep(0.5)
    if live.read_physics().speed_kmh != 300:
        failures.append("UDP loopback 未收到封包")
    if not live.is_running():
        failures.append("剛收包 is_running 應為 True")
    sock.close()
    live.close()

    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        return 1
    print("PASS  (封包解析 + 胎溫重排 + spline + 錄圈 + UDP loopback 全部正確)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
