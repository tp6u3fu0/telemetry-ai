"""iRacing 遙測來源：官方 SDK（memory-mapped file，經 pyirsdk）→ 統一 snapshot。

對應關係：
    spline            <- LapDistPct（iRacing 原生就是 0~1 圈內位置）
    速度              <- Speed (m/s → km/h)
    方向盤            <- SteeringWheelAngle / SteeringWheelAngleMax（正規化到 -1~1）
    G 值              <- LatAccel / LongAccel (m/s² → g)
    世界座標          <- Lat/Lon GPS 以等距圓柱投影轉局部公尺（賽道地圖用）
    有效圈            <- PlayerTrackSurface（OffTrack / NotInWorld 視為無效）
    胎溫              <- {LF,RF,LR,RR}tempCM（胎面中央溫度）

測試時可注入假的 ir 物件（dict-like），不需要 iRacing 執行中。
"""
from __future__ import annotations

import math
import re

from data_store.opponents import OpponentSample
from telemetry_listener.shared_memory import (GraphicsSnapshot,
                                              PhysicsSnapshot, StaticInfo)

_G = 9.80665
_EARTH_R = 6371000.0

# PlayerTrackSurface（irsdk_TrkLoc）
_NOT_IN_WORLD = -1
_OFF_TRACK = 0

_SESSION_TYPE = {"practice": 0, "offline testing": 0, "warmup": 0,
                 "qualify": 1, "lone qualify": 1, "open qualify": 1,
                 "race": 2}


class IRacingReader:
    game = "iracing"
    display_name = "iRacing"

    def __init__(self, ir=None):
        if ir is None:
            import irsdk
            ir = irsdk.IRSDK()
            self._external = False
        else:
            self._external = True   # 測試注入
        self.ir = ir
        self._lat0 = None
        self._lon0 = None
        # 航位推算狀態（iRacing 即時遙測沒有 Lat/Lon，用速度+朝向積分出軌跡）
        self._dr_x = 0.0
        self._dr_y = 0.0
        self._dr_t = None

    def close(self) -> None:
        if not self._external:
            try:
                self.ir.shutdown()
            except Exception:
                pass

    def is_running(self) -> bool:
        if self._external:
            return True
        try:
            if not self.ir.is_initialized and not self.ir.startup():
                return False
            return bool(self.ir.is_connected)
        except Exception:
            return False

    def _freeze(self) -> None:
        """凍結變數緩衝，避免讀到更新到一半的撕裂值（會造成 spline 亂跳）。"""
        try:
            self.ir.freeze_var_buffer_latest()
        except Exception:
            pass

    def _get(self, name, default=None):
        try:
            v = self.ir[name]
            return default if v is None else v
        except Exception:
            return default

    def read_physics(self) -> PhysicsSnapshot:
        self._freeze()
        steer_max = self._get("SteeringWheelAngleMax", 0.0) or 0.0
        steer = self._get("SteeringWheelAngle", 0.0) or 0.0
        return PhysicsSnapshot(
            packet_id=int(self._get("SessionTick", 0) or 0),
            throttle=float(self._get("Throttle", 0.0) or 0.0),
            brake=float(self._get("Brake", 0.0) or 0.0),
            gear=int(self._get("Gear", 0) or 0),      # iRacing 原生 -1=R 0=N，同我們的慣例
            rpm=int(self._get("RPM", 0) or 0),
            steer_angle=(steer / steer_max) if steer_max else 0.0,
            speed_kmh=float(self._get("Speed", 0.0) or 0.0) * 3.6,
            acc_lat=float(self._get("LatAccel", 0.0) or 0.0) / _G,
            acc_lon=float(self._get("LongAccel", 0.0) or 0.0) / _G,
            tyre_temp=(
                float(self._get("LFtempCM", 0.0) or 0.0),
                float(self._get("RFtempCM", 0.0) or 0.0),
                float(self._get("LRtempCM", 0.0) or 0.0),
                float(self._get("RRtempCM", 0.0) or 0.0),
            ),
            tyre_pressure=(0.0, 0.0, 0.0, 0.0),  # iRacing 不提供即時胎壓
        )

    def _world_xy(self) -> tuple:
        # GPS 路徑（部分 session 才有；0,0 視為無資料）
        lat = self._get("Lat")
        lon = self._get("Lon")
        if lat and lon:
            if self._lat0 is None:
                self._lat0, self._lon0 = lat, lon
            x = math.radians(lon - self._lon0) * _EARTH_R * math.cos(math.radians(self._lat0))
            y = math.radians(lat - self._lat0) * _EARTH_R
            return x, y
        return self._dead_reckon()

    def _dead_reckon(self) -> tuple:
        """VelocityX/Y（車身座標系）+ YawNorth 積分出世界軌跡。

        一圈內的漂移約數公尺，畫地圖綽綽有餘。"""
        t = self._get("SessionTime")
        yaw = self._get("YawNorth")
        vx = self._get("VelocityX")
        vy = self._get("VelocityY")
        if t is None or yaw is None or vx is None:
            return self._dr_x, self._dr_y
        if self._dr_t is not None:
            dt = t - self._dr_t
            if 0 < dt < 0.5:
                vy = vy or 0.0
                self._dr_x += (vx * math.sin(yaw) + vy * math.cos(yaw)) * dt  # 東向
                self._dr_y += (vx * math.cos(yaw) - vy * math.sin(yaw)) * dt  # 北向
        self._dr_t = t
        return self._dr_x, self._dr_y

    def _session_type(self) -> int:
        try:
            num = self._get("SessionNum", 0) or 0
            sessions = self.ir["SessionInfo"]["Sessions"]
            name = str(sessions[num]["SessionType"]).lower()
            return _SESSION_TYPE.get(name, 0)
        except Exception:
            return 0

    def read_graphics(self) -> GraphicsSnapshot:
        self._freeze()
        on_track = bool(self._get("IsOnTrack", False))
        surface = int(self._get("PlayerTrackSurface", _NOT_IN_WORLD)
                      if self._get("PlayerTrackSurface") is not None else _NOT_IN_WORLD)
        cur_s = self._get("LapCurrentLapTime", 0.0) or 0.0
        last_s = self._get("LapLastLapTime", 0.0) or 0.0
        best_s = self._get("LapBestLapTime", 0.0) or 0.0
        wx, wy = self._world_xy()
        return GraphicsSnapshot(
            packet_id=int(self._get("SessionTick", 0) or 0),
            status=2 if on_track else 0,          # 2 = LIVE（與 ACC 慣例一致）
            session_type=self._session_type(),
            completed_laps=max(0, int(self._get("LapCompleted", 0) or 0)),
            current_lap_time_ms=max(0, int(cur_s * 1000)),
            last_lap_time_ms=max(0, int(last_s * 1000)),
            best_lap_time_ms=max(0, int(best_s * 1000)),
            spline_position=float(self._get("LapDistPct", 0.0) or 0.0),
            is_valid_lap=surface not in (_NOT_IN_WORLD, _OFF_TRACK),
            is_in_pit=bool(self._get("OnPitRoad", False)),
            current_sector=0,                     # iRacing 不直接提供，分析端不依賴
            world_x=wx,
            world_y=wy,
        )

    def track_length_m(self) -> float:
        """WeekendInfo.TrackLength 是 '3.93 km' 這種字串。"""
        try:
            m = re.match(r"([\d.]+)\s*km",
                         str(self.ir["WeekendInfo"]["TrackLength"]))
            return float(m.group(1)) * 1000 if m else 0.0
        except Exception:
            return 0.0

    def _driver_names(self) -> dict:
        """carIdx -> 車手名（pace car 排除）。"""
        try:
            info = self.ir["DriverInfo"]
            return {d["CarIdx"]: str(d.get("UserName") or f"Car {d['CarIdx']}")
                    for d in info["Drivers"]
                    if not d.get("CarIsPaceCar")}
        except Exception:
            return {}

    def read_opponents(self) -> list:
        """CarIdx 陣列：spline/圈速/檔位/轉速。iRacing 不提供對手踏板；
        速度由 tracker 從 spline 位移推導。"""
        self._freeze()
        pcts = self._get("CarIdxLapDistPct")
        if not pcts:
            return []
        player = None
        try:
            player = self.ir["DriverInfo"]["DriverCarIdx"]
        except Exception:
            pass
        completed = self._get("CarIdxLapCompleted") or []
        last_times = self._get("CarIdxLastLapTime") or []
        gears = self._get("CarIdxGear") or []
        rpms = self._get("CarIdxRPM") or []
        pits = self._get("CarIdxOnPitRoad") or []
        surfaces = self._get("CarIdxTrackSurface") or []
        names = self._driver_names()
        out = []
        for i, pct in enumerate(pcts):
            if i == player or i not in names:
                continue
            surface = surfaces[i] if i < len(surfaces) else -1
            if surface is None or surface < 0 or pct is None or pct < 0:
                continue    # 不在世界中
            last_s = last_times[i] if i < len(last_times) else 0
            out.append(OpponentSample(
                car_key=f"ir_{i}",
                name=names[i],
                spline=float(pct),
                laps=max(0, int(completed[i] if i < len(completed) else 0)),
                last_lap_ms=max(0, int((last_s or 0) * 1000)),
                speed_kmh=None,     # 由 tracker 推導
                gear=int(gears[i]) if i < len(gears) else None,
                rpm=int(rpms[i]) if i < len(rpms) else None,
                in_pit=bool(pits[i]) if i < len(pits) else False,
                on_track=surface >= 1,
            ))
        return out

    def read_static(self) -> StaticInfo:
        track = car = player = ""
        sectors = 0
        try:
            wk = self.ir["WeekendInfo"]
            track = str(wk.get("TrackDisplayName") or wk.get("TrackName") or "")
        except Exception:
            pass
        try:
            info = self.ir["DriverInfo"]
            idx = info["DriverCarIdx"]
            driver = next(d for d in info["Drivers"] if d["CarIdx"] == idx)
            car = str(driver.get("CarScreenNameShort")
                      or driver.get("CarScreenName") or "")
            player = str(driver.get("UserName") or "")
        except Exception:
            pass
        try:
            sectors = len(self.ir["SplitTimeInfo"]["Sectors"])
        except Exception:
            pass
        return StaticInfo(
            sm_version="irsdk",
            car_model=car,
            track=track,
            player_name=player,
            sector_count=sectors,
        )
