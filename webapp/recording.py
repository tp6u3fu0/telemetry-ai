"""App 內錄製服務：背景 thread 取樣 + LapRecorder，支援多遊戲自動偵測。

狀態機：idle → waiting（等任一支援的遊戲進賽道）→ recording → idle
UI 以 /api/record/status 輪詢 status dict（單 writer、讀取靠 GIL，夠用）。
"""
from __future__ import annotations

import threading
import time

from data_store.db import TelemetryDB
from data_store.opponents import OpponentTracker
from data_store.recorder import LapRecorder
from sources import detect_live, open_all
from telemetry_listener.live_console import format_laptime

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
        readers = []
        db = None
        reader = None
        try:
            readers = open_all()
            # 等待任一遊戲上賽道（status == 2 LIVE）
            while not self._stop.is_set():
                reader = detect_live(readers)
                if reader is not None:
                    break
                self.status = {"phase": "waiting"}
                time.sleep(0.5)
            if self._stop.is_set():
                self.status = {"phase": "idle", "message": "已取消（未偵測到遊戲）"}
                return
            # 只留偵測到的來源，其他關掉
            for r in readers:
                if r is not reader:
                    r.close()
            readers = [reader]

            static = reader.read_static()
            gfx = reader.read_graphics()
            db = TelemetryDB(db_path)
            session_id = db.create_session(
                track=static.track, car_model=static.car_model,
                player=static.player_name, session_type=gfx.session_type,
                game=reader.game)
            recorder = LapRecorder(db, session_id)
            # 對手遙測（reader 有支援才啟用；F1 25 / iRacing）
            tracker = None
            if hasattr(reader, "read_opponents"):
                track_len = (reader.track_length_m()
                             if hasattr(reader, "track_length_m") else 0.0)
                tracker = OpponentTracker(db, session_id,
                                          track_length_m=track_len)

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
                if tracker is not None:
                    if not tracker.track_length_m and hasattr(reader, "track_length_m"):
                        tracker.track_length_m = reader.track_length_m()
                    tracker.process(reader.read_opponents(), time.monotonic())
                self.status.update({
                    "phase": "recording",
                    "session_id": session_id,
                    "game": reader.game,
                    "game_name": reader.display_name,
                    "track": static.track,
                    "car": static.car_model,
                    "current_lap": gfx.completed_laps + 1,
                    "spline_pct": round(gfx.spline_position * 100, 1),
                    "current_time": format_laptime(gfx.current_lap_time_ms),
                    "laps_saved": recorder.laps_saved,
                    "points": recorder.current_point_count,
                    "opp_laps": tracker.laps_saved if tracker else 0,
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
            for r in readers:
                try:
                    r.close()
                except Exception:
                    pass


service = RecordingService()
