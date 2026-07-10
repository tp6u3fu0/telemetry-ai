"""App 內錄製服務：在背景 thread 跑 shared memory 取樣 + LapRecorder。

狀態機：idle → waiting（等 ACC 進賽道）→ recording → idle
UI 以 /api/record/status 輪詢 status dict（單 writer、讀取靠 GIL，夠用）。
"""
from __future__ import annotations

import threading
import time

from data_store.db import TelemetryDB
from data_store.recorder import LapRecorder
from telemetry_listener.live_console import format_laptime
from telemetry_listener.shared_memory import SharedMemoryReader

_HZ = 50.0


class RecordingService:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.status: dict = {"phase": "idle"}

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, db_path: str) -> tuple[bool, str]:
        if self.active:
            return False, "已在錄製中"
        self._stop.clear()
        self.status = {"phase": "waiting"}
        self._thread = threading.Thread(target=self._run, args=(db_path,),
                                        daemon=True)
        self._thread.start()
        return True, "已開始"

    def stop(self) -> tuple[bool, str]:
        if not self.active:
            return False, "沒有進行中的錄製"
        self._stop.set()
        self._thread.join(timeout=5.0)
        return True, "已停止"

    def _run(self, db_path: str) -> None:
        reader = None
        db = None
        try:
            reader = SharedMemoryReader()
            # 等待 ACC 上賽道（status == 2 LIVE）
            while not self._stop.is_set():
                if reader.is_acc_running() and reader.read_graphics().status == 2:
                    break
                self.status = {"phase": "waiting"}
                time.sleep(0.5)
            if self._stop.is_set():
                self.status = {"phase": "idle", "message": "已取消（未等到 ACC）"}
                return

            static = reader.read_static()
            gfx = reader.read_graphics()
            db = TelemetryDB(db_path)
            session_id = db.create_session(
                track=static.track, car_model=static.car_model,
                player=static.player_name, session_type=gfx.session_type,
                game="acc")
            recorder = LapRecorder(db, session_id)

            def on_lap(n, t, valid):
                self.status["last_lap"] = (
                    f"Lap {n}: {format_laptime(t)}"
                    f"{'' if valid else '（無效）'}")

            recorder.on_lap_saved = on_lap
            period = 1.0 / _HZ
            while not self._stop.is_set():
                phys = reader.read_physics()
                gfx = reader.read_graphics()
                recorder.process_sample(phys, gfx)
                self.status.update({
                    "phase": "recording",
                    "session_id": session_id,
                    "track": static.track,
                    "car": static.car_model,
                    "current_lap": gfx.completed_laps + 1,
                    "spline_pct": round(gfx.spline_position * 100, 1),
                    "current_time": format_laptime(gfx.current_lap_time_ms),
                    "laps_saved": recorder.laps_saved,
                    "points": recorder.current_point_count,
                })
                time.sleep(period)

            recorder.finalize()
            self.status = {
                "phase": "idle",
                "message": f"錄製完成：{recorder.laps_saved} 圈（session #{session_id}）",
                "session_id": session_id,
            }
        except Exception as exc:
            self.status = {"phase": "error", "error": repr(exc)}
        finally:
            if db is not None:
                db.close()
            if reader is not None:
                reader.close()


service = RecordingService()
