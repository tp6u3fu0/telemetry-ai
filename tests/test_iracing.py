"""iRacing adapter 測試：假 ir 物件驗證欄位映射 + 完整錄一圈進 SQLite。

執行：uv run python tests/test_iracing.py
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_store.db import TelemetryDB              # noqa: E402
from data_store.recorder import LapRecorder        # noqa: E402
from sources.iracing import IRacingReader          # noqa: E402


class FakeIR:
    """dict-like 假 irsdk，模擬 60Hz 的變數讀取。"""

    def __init__(self):
        self.vars = {
            "SessionTick": 1000,
            "Speed": 50.0,                 # m/s → 180 km/h
            "Throttle": 0.8, "Brake": 0.0,
            "Gear": 4, "RPM": 6500.0,
            "SteeringWheelAngle": 0.3, "SteeringWheelAngleMax": 1.2,
            "LatAccel": 19.6133, "LongAccel": -9.80665,   # 2g / -1g
            "Lat": 34.915, "Lon": 134.219,                # 岡山附近
            "LapDistPct": 0.25,
            "Lap": 3, "LapCompleted": 2,
            "LapCurrentLapTime": 45.678, "LapLastLapTime": 92.5,
            "LapBestLapTime": 91.2,
            "IsOnTrack": True, "OnPitRoad": False,
            "PlayerTrackSurface": 3,       # onTrack
            "SessionNum": 0,
            "WeekendInfo": {"TrackName": "okayama full",
                            "TrackDisplayName": "Okayama International Circuit"},
            "DriverInfo": {"DriverCarIdx": 5, "Drivers": [
                {"CarIdx": 5, "CarScreenNameShort": "MX-5", "UserName": "Chen"},
                {"CarIdx": 7, "CarScreenNameShort": "GR86", "UserName": "Other"},
            ]},
            "SessionInfo": {"Sessions": [{"SessionType": "Practice"}]},
            "SplitTimeInfo": {"Sectors": [{}, {}, {}]},
            "LFtempCM": 62.0, "RFtempCM": 65.0, "LRtempCM": 58.0, "RRtempCM": 60.0,
        }

    def __getitem__(self, key):
        return self.vars.get(key)


def main() -> int:
    failures = []
    fake = FakeIR()
    r = IRacingReader(ir=fake)

    # -- 欄位映射 --
    p = r.read_physics()
    if abs(p.speed_kmh - 180.0) > 0.01:
        failures.append(f"速度換算錯: {p.speed_kmh}")
    if abs(p.steer_angle - 0.25) > 0.001:
        failures.append(f"方向盤正規化錯: {p.steer_angle}")
    if abs(p.acc_lat - 2.0) > 0.01 or abs(p.acc_lon + 1.0) > 0.01:
        failures.append(f"G 值換算錯: {p.acc_lat}, {p.acc_lon}")
    if p.gear != 4 or p.rpm != 6500:
        failures.append(f"檔位/轉速錯: {p.gear}, {p.rpm}")
    if p.tyre_temp != (62.0, 65.0, 58.0, 60.0):
        failures.append(f"胎溫順序錯 (FL,FR,RL,RR): {p.tyre_temp}")

    g = r.read_graphics()
    if g.status != 2:
        failures.append(f"IsOnTrack 應為 LIVE: {g.status}")
    if g.spline_position != 0.25 or g.completed_laps != 2:
        failures.append(f"spline/圈數錯: {g.spline_position}, {g.completed_laps}")
    if g.current_lap_time_ms != 45678 or g.last_lap_time_ms != 92500:
        failures.append(f"圈時間換算錯: {g.current_lap_time_ms}, {g.last_lap_time_ms}")
    if not g.is_valid_lap:
        failures.append("onTrack 應為有效圈")

    # 世界座標：第一筆為原點，往北 100m 應反映在 y
    g0 = r.read_graphics()
    fake.vars["Lat"] += 100 / 6371000 * 180 / math.pi
    g1 = r.read_graphics()
    if abs((g1.world_y - g0.world_y) - 100) > 1:
        failures.append(f"GPS→公尺換算錯: dy={g1.world_y - g0.world_y}")

    # 出界 → 無效圈；NotInWorld 也無效
    fake.vars["PlayerTrackSurface"] = 0
    if r.read_graphics().is_valid_lap:
        failures.append("offTrack 應為無效圈")
    fake.vars["PlayerTrackSurface"] = 3

    s = r.read_static()
    if s.track != "okayama full" or s.car_model != "MX-5" or s.player_name != "Chen":
        failures.append(f"static 錯: {s}")
    if s.sector_count != 3:
        failures.append(f"sector 數錯: {s.sector_count}")

    # -- 完整錄一圈（recorder 整合）--
    db = TelemetryDB(os.path.join(tempfile.mkdtemp(), "ir.sqlite3"))
    sid = db.create_session(s.track, s.car_model, s.player_name, 0, game="iracing")
    rec = LapRecorder(db, sid)
    lap_ms = 92_000
    fake.vars["LapCompleted"] = 0
    for t in range(0, lap_ms, 20):                     # 跑完第 1 圈
        fake.vars["SessionTick"] += 1
        fake.vars["LapCurrentLapTime"] = t / 1000
        fake.vars["LapDistPct"] = t / lap_ms
        rec.process_sample(r.read_physics(), r.read_graphics())
    fake.vars["SessionTick"] += 1                       # 過線
    fake.vars["LapCompleted"] = 1
    fake.vars["LapCurrentLapTime"] = 0.0
    fake.vars["LapLastLapTime"] = lap_ms / 1000
    fake.vars["LapDistPct"] = 0.0
    rec.process_sample(r.read_physics(), r.read_graphics())
    rec.finalize()

    laps = db.list_laps(sid)
    if len(laps) < 1 or laps[0]["lap_time_ms"] != lap_ms or not laps[0]["is_valid"]:
        failures.append(f"iRacing 圈錄製錯: {[dict(l) for l in laps]}")
    game = db.conn.execute("SELECT game FROM sessions WHERE session_id=?",
                           (sid,)).fetchone()["game"]
    if game != "iracing":
        failures.append(f"session game 欄位錯: {game}")
    db.close()

    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        return 1
    print("PASS  (欄位映射 + GPS 座標 + 有效圈判定 + 完整錄圈全部正確)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
