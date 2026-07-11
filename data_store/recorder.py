"""圈次錄製邏輯：把 shared memory 取樣切成一圈一圈存進 SQLite。

設計成 process_sample(phys, gfx) 純邏輯 + 外部取樣迴圈，方便離線測試。

圈界偵測：以 graphics 頁的 completedLaps 遞增為準（spline 歸零有抖動，僅作參考）。
圈時間軸：直接用遊戲內的 iCurrentTime（目前圈進行時間），不用牆上時鐘，
這樣逐點資料天生就對齊圈內時間。
有效圈判定：isValidLap 在圈中任一時刻為 False，該圈即記為 invalid。
"""
from __future__ import annotations

from typing import Optional

from telemetry_listener.shared_memory import GraphicsSnapshot, PhysicsSnapshot

from .db import TelemetryDB

_STATUS_LIVE = 2


class LapRecorder:
    def __init__(self, db: TelemetryDB, session_id: int):
        self.db = db
        self.session_id = session_id
        self.laps_saved = 0
        self.on_lap_saved = None        # callback(lap_number, lap_time_ms, is_valid)

        self._points: list = []
        self._prev_completed: Optional[int] = None
        self._lap_valid = True
        self._lap_partial = False       # 起錄時已在圈中（資料不完整）
        self._last_t = -1
        self._last_packet = -1

    @property
    def current_point_count(self) -> int:
        return len(self._points)

    def process_sample(self, phys: PhysicsSnapshot, gfx: GraphicsSnapshot) -> None:
        if gfx.status != _STATUS_LIVE:
            return

        # 第一筆樣本：初始化圈狀態
        if self._prev_completed is None:
            self._prev_completed = gfx.completed_laps
            self._lap_partial = gfx.current_lap_time_ms > 1000  # 起錄時該圈已進行中
            self._lap_valid = gfx.is_valid_lap

        # session 重置（回 pit 重新出發、換 session 等 completedLaps 變小）
        if gfx.completed_laps < self._prev_completed:
            self._reset_lap(gfx)
            return

        # 圈界：completedLaps 遞增 → 關閉上一圈
        if gfx.completed_laps > self._prev_completed:
            # 圈速：遊戲回報值優先；缺漏（iRacing 的 LapLastLapTime 過線瞬間
            # 常還沒更新、或給 -1）時，用緩衝最後一點的圈內時間 ≈ 圈時。
            # 不再用「最後點 − 目前圈時間」——iRacing 的 LapCompleted 遞增與
            # LapCurrentLapTime 歸零有時間差，會讓該式算出接近 0 而失效。
            reported = gfx.last_lap_time_ms or 0
            est = self._points[-1][0] if self._points else 0
            complete = not self._lap_partial
            if reported > 5000:
                lap_time = reported
            elif complete and est > 5000:
                lap_time = est
            else:
                lap_time = None
            self._close_lap(lap_number=self._prev_completed + 1,
                            lap_time_ms=lap_time,
                            is_complete=complete)
            self._prev_completed = gfx.completed_laps
            self._lap_partial = False
            self._lap_valid = True

        if not gfx.is_valid_lap:
            self._lap_valid = False

        # 去重：物理封包沒更新且圈時間沒變就不重複記
        if phys.packet_id == self._last_packet and gfx.current_lap_time_ms == self._last_t:
            return
        self._last_packet = phys.packet_id
        self._last_t = gfx.current_lap_time_ms

        self._points.append((
            gfx.current_lap_time_ms,
            gfx.spline_position,
            phys.speed_kmh,
            phys.throttle,
            phys.brake,
            phys.steer_angle,
            phys.gear,
            phys.rpm,
            gfx.world_x,
            gfx.world_y,
            phys.acc_lat,
            phys.acc_lon,
            *phys.tyre_temp,
            *phys.tyre_pressure,
        ))

    def finalize(self) -> None:
        """錄製結束：把進行中的圈存成未完成圈。"""
        if self._points and self._prev_completed is not None:
            self._close_lap(lap_number=self._prev_completed + 1,
                            lap_time_ms=None, is_complete=False)

    def _close_lap(self, lap_number: int, lap_time_ms, is_complete: bool) -> None:
        if not self._points:
            return
        self.db.save_lap(self.session_id, lap_number, lap_time_ms,
                         is_valid=self._lap_valid and is_complete,
                         is_complete=is_complete, points=self._points)
        self.laps_saved += 1
        if self.on_lap_saved:
            self.on_lap_saved(lap_number, lap_time_ms, self._lap_valid and is_complete)
        self._points = []

    def _reset_lap(self, gfx: GraphicsSnapshot) -> None:
        self._points = []
        self._prev_completed = gfx.completed_laps
        self._lap_valid = gfx.is_valid_lap
        self._lap_partial = gfx.current_lap_time_ms > 1000
        self._last_t = -1
