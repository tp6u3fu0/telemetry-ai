"""階段一驗收工具：即時在 console 顯示 ACC 遙測。

用法（在專案根目錄）：
    python -m telemetry_listener.live_console                # shared memory + broadcasting 都開
    python -m telemetry_listener.live_console --shm-only     # 只讀 shared memory
    python -m telemetry_listener.live_console --udp-only     # 只連 Broadcasting API
    python -m telemetry_listener.live_console --port 9000 --password asd

驗收方式：進 ACC 跑一圈，對照遊戲內 HUD 核對速度/油門/煞車/檔位/RPM/圈速。
"""
from __future__ import annotations

import argparse
import sys
import time

from .broadcast.client import BroadcastClient
from .shared_memory import SharedMemoryReader


def format_laptime(ms) -> str:
    if not ms or ms <= 0 or ms >= 2147483647:
        return "--:--.---"
    m, rem = divmod(ms, 60000)
    s, milli = divmod(rem, 1000)
    return f"{m}:{s:02d}.{milli:03d}"


def format_gear(gear: int) -> str:
    return {-1: "R", 0: "N"}.get(gear, str(gear))


def main() -> int:
    parser = argparse.ArgumentParser(description="ACC 即時遙測 console 顯示")
    parser.add_argument("--shm-only", action="store_true", help="只讀 shared memory")
    parser.add_argument("--udp-only", action="store_true", help="只連 Broadcasting UDP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000, help="broadcasting.json 的 udpListenerPort")
    parser.add_argument("--password", default="asd", help="broadcasting.json 的 connectionPassword")
    parser.add_argument("--interval", type=int, default=100, help="UDP realtime 更新間隔 (ms)")
    parser.add_argument("--hz", type=float, default=20.0, help="console 更新頻率")
    args = parser.parse_args()

    use_shm = not args.udp_only
    use_udp = not args.shm_only

    shm = None
    if use_shm:
        shm = SharedMemoryReader()
        if not shm.is_acc_running():
            print("[shm] 尚未偵測到 ACC（shared memory 為空），啟動遊戲並進入賽道後會自動開始顯示。")

    client = None
    udp_state = {"car": None, "track": None, "session": None}
    if use_udp:
        client = BroadcastClient(host=args.host, port=args.port,
                                 connection_password=args.password,
                                 update_interval_ms=args.interval)
        client.on_registration = lambda m: print(
            f"\n[udp] registration: success={m.success} id={m.connection_id} "
            f"read_only={m.read_only} err={m.error_message!r}")
        client.on_track_data = lambda m: print(
            f"\n[udp] track: {m.track_name} ({m.track_meters} m)")
        client.on_entry_list_car = lambda m: print(
            f"\n[udp] car #{m.race_number} {m.team_name} (index={m.car_index})")

        def on_car_update(m):
            udp_state["car"] = m

        def on_rt_update(m):
            udp_state["session"] = m

        client.on_realtime_car_update = on_car_update
        client.on_realtime_update = on_rt_update
        client.start()
        print(f"[udp] 連線至 {args.host}:{args.port}，等待 ACC 回應 registration...")

    print("按 Ctrl+C 結束。\n")
    period = 1.0 / args.hz
    try:
        while True:
            parts = []
            if shm is not None:
                phys = shm.read_physics()
                gfx = shm.read_graphics()
                parts.append(
                    f"[SHM] {phys.speed_kmh:5.1f} km/h | G:{format_gear(phys.gear)} "
                    f"| {phys.rpm:5d} rpm | T:{phys.throttle*100:5.1f}% "
                    f"| B:{phys.brake*100:5.1f}% | S:{phys.steer_angle:+.2f} "
                    f"| lap {gfx.completed_laps + 1} @ {gfx.spline_position*100:5.1f}% "
                    f"| cur {format_laptime(gfx.current_lap_time_ms)} "
                    f"| last {format_laptime(gfx.last_lap_time_ms)} "
                    f"| {'VALID' if gfx.is_valid_lap else 'INVALID'}"
                )
            if client is not None:
                car = udp_state["car"]
                if car is not None:
                    parts.append(
                        f"[UDP] {car.speed_kmh:3d} km/h | G:{format_gear(car.gear)} "
                        f"| spline {car.spline_position*100:5.1f}% | laps {car.laps} "
                        f"| cur {format_laptime(car.current_lap.laptime_ms)} "
                        f"| last {format_laptime(car.last_lap.laptime_ms)}"
                    )
                elif not client.connected:
                    parts.append("[UDP] 未連線（ACC 需在賽道上且 broadcasting.json 已設定）")
            line = "  ||  ".join(parts) if parts else "(無資料來源)"
            sys.stdout.write("\r" + line.ljust(178)[:178])
            sys.stdout.flush()
            time.sleep(period)
    except KeyboardInterrupt:
        print("\n結束。")
    finally:
        if client is not None:
            client.stop()
        if shm is not None:
            shm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
