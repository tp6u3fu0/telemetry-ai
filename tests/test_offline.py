"""離線端對端測試：假 server + 真 client，驗證 handshake 與封包解析。

執行：python tests/test_offline.py
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telemetry_listener.broadcast.client import BroadcastClient  # noqa: E402

PORT = 19100


def main() -> int:
    server = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "..",
                                      "tools", "fake_broadcast_server.py"),
         "--port", str(PORT), "--duration", "6"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(0.5)

    received = {"reg": None, "track": None, "entry_car": None, "car_updates": []}
    client = BroadcastClient(port=PORT, update_interval_ms=100)
    client.on_registration = lambda m: received.__setitem__("reg", m)
    client.on_track_data = lambda m: received.__setitem__("track", m)
    client.on_entry_list_car = lambda m: received.__setitem__("entry_car", m)
    client.on_realtime_car_update = lambda m: received["car_updates"].append(m)
    client.start()

    time.sleep(3.0)
    client.stop()
    server.wait(timeout=10)

    failures = []

    reg = received["reg"]
    if reg is None or not reg.success or reg.connection_id != 7777:
        failures.append(f"registration failed: {reg}")

    track = received["track"]
    if track is None or track.track_name != "fake_monza" or track.track_meters != 5793:
        failures.append(f"track data mismatch: {track}")

    car = received["entry_car"]
    if car is None or car.race_number != 42 or car.drivers[0].short_name != "TST":
        failures.append(f"entry list car mismatch: {car}")

    updates = received["car_updates"]
    if len(updates) < 10:
        failures.append(f"too few car updates: {len(updates)}")
    else:
        u = updates[-1]
        if not (0.0 <= u.spline_position <= 1.0):
            failures.append(f"spline out of range: {u.spline_position}")
        if not (100 <= u.speed_kmh <= 240):
            failures.append(f"speed out of range: {u.speed_kmh}")
        if not (2 <= u.gear <= 5):
            failures.append(f"gear out of range: {u.gear}")
        if u.best_session_lap.laptime_ms != 90123:
            failures.append(f"best lap parse error: {u.best_session_lap.laptime_ms}")
        if u.best_session_lap.splits_ms != [30000, 30000, 30123]:
            failures.append(f"splits parse error: {u.best_session_lap.splits_ms}")
        if u.current_lap.laptime_ms is not None:
            failures.append(f"in-progress lap should be None: {u.current_lap.laptime_ms}")
        # spline 必須隨時間單調前進（在同一圈內）
        splines = [x.spline_position for x in updates[:5]]
        if sorted(splines) != splines:
            failures.append(f"spline not increasing: {splines}")

    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        return 1
    print(f"PASS  (registration + track data + entry list + {len(updates)} car updates parsed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
