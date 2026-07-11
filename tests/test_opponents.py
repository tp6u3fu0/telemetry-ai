"""對手遙測測試：F1 全車解析、iRacing CarIdx、tracker 切圈與速度推導。

執行：uv run python tests/test_opponents.py
"""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_store.db import TelemetryDB              # noqa: E402
from data_store.opponents import (                 # noqa: E402
    OpponentSample, OpponentTracker)
from sources.f1_25 import F1Reader                 # noqa: E402
from sources.iracing import IRacingReader          # noqa: E402
from test_f1 import (                              # noqa: E402
    PLAYER, header, lap_packet, session_packet, telemetry_packet)
from test_iracing import FakeIR                    # noqa: E402


def participants_packet(names):
    """F1 24 風格 60 bytes/車（parser 用動態 stride，不依賴精確版本）。"""
    body = struct.pack("<B", len(names))
    for i in range(22):
        name = (names[i] if i < len(names) else "").encode()[:47]
        car = struct.pack("<7B", 1, 0, 0, 0, 0, i, 0)
        car += name + b"\x00" * (48 - len(name))
        car += struct.pack("<BBHB", 0, 0, 0, 1)
        assert len(car) == 60
        body += car
    return header(4, 50) + body


def multi_car_lap_packet(frame, cars):
    """cars: {idx: dict(last, cur, dist, lap, status, result)}"""
    blob = b""
    for i in range(22):
        c = cars.get(i)
        if c is None:
            car = b"\x00" * 57
        else:
            car = struct.pack("<II", c.get("last", 0), c.get("cur", 0))
            car += struct.pack("<HBHB", 0, 0, 0, 0) + struct.pack("<HBHB", 0, 0, 0, 0)
            car += struct.pack("<fff", c["dist"], 0, 0)
            car += struct.pack("<6B", 1, c.get("lap", 1), c.get("pit", 0), 0, 0, 0)
            car += struct.pack("<6B", 0, 0, 0, 0, 0, 0)
            car += struct.pack("<BB", c.get("status", 1), c.get("result", 2))
            car += struct.pack("<BHHB", 0, 0, 0, 0) + struct.pack("<fB", 0, 0)
        blob += car
    return header(2, frame) + blob + b"\x00\x00"


def main() -> int:
    failures = []

    # -- F1：全車解析 + 車手名 --
    r = F1Reader(listen=False)
    r.parse_datagram(session_packet(1, track_len=5300))
    names = [""] * 22
    names[5] = "M. VERSTAPPEN"
    r.parse_datagram(participants_packet(names))
    r.parse_datagram(telemetry_packet(2, 280, 0.95, 0.0, 7, 11500))  # 只有玩家格有值
    r.parse_datagram(multi_car_lap_packet(3, {
        PLAYER: {"dist": 1000.0, "lap": 2},
        5: {"dist": 2650.0, "lap": 3, "last": 89000, "status": 1, "result": 2},
        9: {"dist": 500.0, "lap": 1, "status": 0, "result": 2},   # 在車庫
        12: {"dist": 100.0, "lap": 1, "result": 3},               # 非 active
    }))
    opps = r.read_opponents()
    keys = {o.car_key: o for o in opps}
    if "f1_5" not in keys:
        failures.append(f"車 5 應在對手清單: {list(keys)}")
    else:
        o = keys["f1_5"]
        if o.name != "M. VERSTAPPEN":
            failures.append(f"participants 名字解析錯: {o.name}")
        if abs(o.spline - 0.5) > 0.001 or o.last_lap_ms != 89000 or o.laps != 2:
            failures.append(f"車 5 資料錯: {o.spline}, {o.last_lap_ms}, {o.laps}")
    if f"f1_{PLAYER}" in keys:
        failures.append("玩家不應出現在對手清單")
    if "f1_9" in keys and keys["f1_9"].on_track:
        failures.append("車庫中的車 on_track 應為 False")
    if "f1_12" in keys:
        failures.append("非 active 的車不應出現")

    # -- iRacing：CarIdx 陣列 --
    fake = FakeIR()
    fake.vars.update({
        "CarIdxLapDistPct": [0.10, 0.55, -1.0, 0.30],
        "CarIdxLapCompleted": [1, 4, 0, 2],
        "CarIdxLastLapTime": [90.0, 85.5, -1.0, 88.0],
        "CarIdxGear": [3, 4, 0, 2],
        "CarIdxRPM": [5000, 6500, 0, 4000],
        "CarIdxOnPitRoad": [False, False, False, True],
        "CarIdxTrackSurface": [3, 3, -1, 1],
    })
    fake.vars["DriverInfo"] = {"DriverCarIdx": 0, "Drivers": [
        {"CarIdx": 0, "UserName": "Me"},
        {"CarIdx": 1, "UserName": "Rival"},
        {"CarIdx": 2, "UserName": "Gone"},
        {"CarIdx": 3, "UserName": "Pitter", "CarIsPaceCar": 0},
    ]}
    fake.vars["WeekendInfo"]["TrackLength"] = "3.20 km"
    ir = IRacingReader(ir=fake)
    opps = {o.car_key: o for o in ir.read_opponents()}
    if "ir_0" in opps:
        failures.append("iRacing 玩家不應在對手清單")
    if "ir_2" in opps:
        failures.append("不在世界中的車不應出現")
    if "ir_1" not in opps or opps["ir_1"].name != "Rival":
        failures.append(f"iRacing 對手解析錯: {list(opps)}")
    else:
        o = opps["ir_1"]
        if o.spline != 0.55 or o.last_lap_ms != 85500 or o.gear != 4:
            failures.append(f"ir_1 欄位錯: {o}")
    if abs(ir.track_length_m() - 3200) > 1:
        failures.append(f"賽道長解析錯: {ir.track_length_m()}")

    # -- tracker：兩圈完整錄製 + 速度推導 --
    db = TelemetryDB(os.path.join(tempfile.mkdtemp(), "opp.sqlite3"))
    sid = db.create_session("test", "car", "me", 0, game="iracing")
    tr = OpponentTracker(db, sid, track_length_m=3200.0, hz=10)
    lap_s = 80.0
    now = 1000.0
    # 跑 2.5 圈（第一次過線前的資料會被丟棄——正確行為）
    steps = int(2.5 * lap_s / 0.05)
    for k in range(steps):
        t = k * 0.05
        spline = (t / lap_s) % 1.0
        laps_done = int(t / lap_s)
        tr.process([OpponentSample(
            car_key="x", name="Rival", spline=spline, laps=laps_done,
            last_lap_ms=80000 if laps_done else 0,
        )], now + t)
    if tr.laps_saved != 1:
        failures.append(f"應存 1 圈完整對手圈（第 2 圈），得 {tr.laps_saved}")
    rows = db.conn.execute(
        "SELECT * FROM laps WHERE driver='Rival'").fetchall()
    if not rows:
        failures.append("對手圈未入庫")
    else:
        lap = rows[0]
        if abs(lap["lap_time_ms"] - 80000) > 200:
            failures.append(f"對手圈速錯: {lap['lap_time_ms']}")
        pts = db.get_lap_points(lap["lap_id"])
        speeds = [p["speed_kmh"] for p in pts if p["speed_kmh"] is not None]
        expect = 3200 / 80 * 3.6   # 144 km/h 等速
        if not speeds or abs(sum(speeds) / len(speeds) - expect) > 5:
            failures.append(f"推導速度錯: 平均 {sum(speeds)/len(speeds) if speeds else None}"
                            f" 應約 {expect:.0f}")
        if len(pts) < 500 or len(pts) > 900:   # 80s × ~8-10Hz（限流生效即可）
            failures.append(f"取樣限流錯: {len(pts)} 點")
    db.close()

    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        return 1
    print("PASS  (F1 全車+車手名、iRacing CarIdx、tracker 切圈/圈速/速度推導/限流)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
