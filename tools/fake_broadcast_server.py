"""模擬 ACC Broadcasting API 的假 server，用於離線驗證 UDP client 與 parser。

用法：
    python tools/fake_broadcast_server.py [--port 9000]

它會等 client 送 registration request，回覆 registration result，
然後以 10Hz 送出模擬的 realtime car update（車子繞著假賽道跑）。
也會回應 entry list / track data 請求。
"""
from __future__ import annotations

import argparse
import math
import socket
import struct
import time


def write_string(text: str) -> bytes:
    encoded = text.encode("utf-8")
    return struct.pack("<H", len(encoded)) + encoded


def make_lap(laptime_ms, splits=(), is_invalid=False) -> bytes:
    out = struct.pack("<iHH", laptime_ms if laptime_ms else 2147483647, 0, 0)
    out += struct.pack("<B", len(splits))
    for s in splits:
        out += struct.pack("<i", s)
    out += struct.pack("<BBBB", int(is_invalid), int(not is_invalid), 0, 0)
    return out


def make_registration_result(connection_id: int) -> bytes:
    return struct.pack("<BiBB", 1, connection_id, 1, 1) + write_string("")


def make_realtime_car_update(t: float) -> bytes:
    """模擬一台車：spline 隨時間前進，速度/檔位跟著變化。"""
    lap_seconds = 30.0
    spline = (t % lap_seconds) / lap_seconds
    speed = int(120 + 100 * math.sin(spline * 2 * math.pi) ** 2)
    gear_display = 2 + int(spline * 4) % 4          # 2~5 檔
    gear_raw = gear_display + 2                     # 對應 parser 的 raw-2 換算
    laps = int(t // lap_seconds)
    out = struct.pack("<BHHB", 3, 0, 0, 1)          # type, carIndex, driverIndex, driverCount
    out += struct.pack("<B", gear_raw)
    out += struct.pack("<fff", 100.0 * spline, 50.0, 0.0)   # worldX, worldY, yaw
    out += struct.pack("<B", 1)                     # car location = TRACK
    out += struct.pack("<HHHH", speed, 1, 1, 1)     # kmh, position, cup, trackPos
    out += struct.pack("<f", spline)
    out += struct.pack("<H", laps)
    out += struct.pack("<i", 0)                     # delta
    out += make_lap(90123, [30000, 30000, 30123])   # best session lap
    out += make_lap(91500, [30500, 30500, 30500])   # last lap
    out += make_lap(None)                           # current lap（進行中）
    return out


def make_track_data(connection_id: int) -> bytes:
    out = struct.pack("<Bi", 5, connection_id)
    out += write_string("fake_monza")
    out += struct.pack("<ii", 999, 5793)
    out += struct.pack("<B", 1)                     # 1 個 camera set
    out += write_string("Drivable") + struct.pack("<B", 1) + write_string("Cockpit")
    out += struct.pack("<B", 1)                     # 1 個 hud page
    out += write_string("Basic HUD")
    return out


def make_entry_list(connection_id: int) -> bytes:
    return struct.pack("<BiHH", 4, connection_id, 1, 0)


def make_entry_list_car() -> bytes:
    out = struct.pack("<BHB", 6, 0, 30)             # carIndex=0, model=30
    out += write_string("Fake Racing Team")
    out += struct.pack("<i", 42)                    # race number
    out += struct.pack("<BBB", 0, 0, 1)             # cup, currentDriver, driverCount
    out += write_string("Test") + write_string("Driver") + write_string("TST")
    out += struct.pack("<BH", 0, 158)               # category, nationality
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--duration", type=float, default=0, help="秒數，0 = 不限")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", args.port))
    sock.settimeout(0.05)
    print(f"[fake-acc] listening on 127.0.0.1:{args.port}")

    client_addr = None
    connection_id = 7777
    start = time.monotonic()
    last_update = 0.0

    while True:
        now = time.monotonic()
        if args.duration and now - start > args.duration:
            break
        try:
            data, addr = sock.recvfrom(4096)
            msg_type = data[0]
            if msg_type == 1:       # register
                client_addr = addr
                sock.sendto(make_registration_result(connection_id), addr)
                print(f"[fake-acc] client registered from {addr}")
            elif msg_type == 10:    # request entry list
                sock.sendto(make_entry_list(connection_id), addr)
                sock.sendto(make_entry_list_car(), addr)
            elif msg_type == 11:    # request track data
                sock.sendto(make_track_data(connection_id), addr)
            elif msg_type == 9:     # unregister
                print("[fake-acc] client unregistered")
                client_addr = None
        except socket.timeout:
            pass
        if client_addr and now - last_update >= 0.1:
            sock.sendto(make_realtime_car_update(now - start), client_addr)
            last_update = now

    sock.close()
    print("[fake-acc] done")


if __name__ == "__main__":
    main()
