"""階段二錄製 CLI：把跑的每一圈存進 SQLite。

用法（ACC 進入賽道後執行）：
    python -m data_store.record
    python -m data_store.record --db data/telemetry.sqlite3 --hz 50

Ctrl+C 結束（進行中的圈會存成未完成圈）。
"""
from __future__ import annotations

import argparse
import sys
import time

from telemetry_listener.live_console import format_laptime
from telemetry_listener.shared_memory import SharedMemoryReader

from .db import TelemetryDB
from .recorder import LapRecorder


def main() -> int:
    parser = argparse.ArgumentParser(description="ACC 圈次遙測錄製")
    parser.add_argument("--db", default="data/telemetry.sqlite3")
    parser.add_argument("--hz", type=float, default=50.0, help="取樣頻率")
    args = parser.parse_args()

    reader = SharedMemoryReader()
    print("等待 ACC（需啟動遊戲並進入賽道）...")
    while not reader.is_acc_running() or reader.read_graphics().status != 2:
        try:
            time.sleep(1.0)
        except KeyboardInterrupt:
            print("取消。")
            return 0

    static = reader.read_static()
    gfx = reader.read_graphics()
    db = TelemetryDB(args.db)
    session_id = db.create_session(track=static.track, car_model=static.car_model,
                                   player=static.player_name,
                                   session_type=gfx.session_type)
    print(f"session #{session_id}: {static.track} / {static.car_model} / {static.player_name}")
    print("開始錄製，按 Ctrl+C 結束。\n")

    recorder = LapRecorder(db, session_id)
    recorder.on_lap_saved = lambda n, t, valid: print(
        f"\n[saved] lap {n}: {format_laptime(t)} {'VALID' if valid else 'INVALID'}")

    period = 1.0 / args.hz
    try:
        while True:
            phys = reader.read_physics()
            gfx = reader.read_graphics()
            recorder.process_sample(phys, gfx)
            sys.stdout.write(
                f"\rlap {gfx.completed_laps + 1} @ {gfx.spline_position*100:5.1f}% "
                f"| cur {format_laptime(gfx.current_lap_time_ms)} "
                f"| {phys.speed_kmh:5.1f} km/h "
                f"| buffered {recorder.current_point_count:6d} pts "
                f"| saved {recorder.laps_saved} laps ")
            sys.stdout.flush()
            time.sleep(period)
    except KeyboardInterrupt:
        recorder.finalize()
        print(f"\n結束。共存了 {recorder.laps_saved} 圈到 {args.db}（session #{session_id}）")
    finally:
        db.close()
        reader.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
