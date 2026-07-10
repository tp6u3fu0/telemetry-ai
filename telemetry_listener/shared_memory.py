"""ACC Shared Memory 讀取器（Windows memory-mapped file）。

ACC 執行時會建立三個共享記憶體頁：
    Local\\acpmf_physics   — 高頻物理資料（油門、煞車、方向盤、RPM、速度...）
    Local\\acpmf_graphics  — 圈速、賽道位置、有效圈 flag、session 狀態...
    Local\\acpmf_static    — 靜態資料（賽道名、車型、玩家名...）

Broadcasting UDP API 沒有踏板/方向盤/RPM，這些只能從這裡拿。

注意：Windows 的 mmap 帶 tagname 開啟時，若該 mapping 不存在會「自動建立」
一塊全零的記憶體，不會報錯。因此判斷 ACC 是否在跑，要看 static 頁的
smVersion 是否為空字串（或 physics packetId 是否持續為 0）。
"""
from __future__ import annotations

import ctypes
import mmap
from dataclasses import dataclass


class _PhysicsPage(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packetId", ctypes.c_int32),
        ("gas", ctypes.c_float),          # 0.0 ~ 1.0
        ("brake", ctypes.c_float),        # 0.0 ~ 1.0
        ("fuel", ctypes.c_float),
        ("gear", ctypes.c_int32),         # 0 = R, 1 = N, 2 = 1檔...
        ("rpms", ctypes.c_int32),
        ("steerAngle", ctypes.c_float),   # -1.0 ~ 1.0（比例，非角度）
        ("speedKmh", ctypes.c_float),
        ("velocity", ctypes.c_float * 3),
        ("accG", ctypes.c_float * 3),
        ("wheelSlip", ctypes.c_float * 4),
        ("wheelLoad", ctypes.c_float * 4),
        ("wheelsPressure", ctypes.c_float * 4),
        ("wheelAngularSpeed", ctypes.c_float * 4),
        ("tyreWear", ctypes.c_float * 4),
        ("tyreDirtyLevel", ctypes.c_float * 4),
        ("tyreCoreTemperature", ctypes.c_float * 4),
        ("camberRAD", ctypes.c_float * 4),
        ("suspensionTravel", ctypes.c_float * 4),
        ("drs", ctypes.c_float),
        ("tc", ctypes.c_float),
        ("heading", ctypes.c_float),
        ("pitch", ctypes.c_float),
        ("roll", ctypes.c_float),
        ("cgHeight", ctypes.c_float),
        ("carDamage", ctypes.c_float * 5),
        ("numberOfTyresOut", ctypes.c_int32),
        ("pitLimiterOn", ctypes.c_int32),
        ("abs", ctypes.c_float),
        ("kersCharge", ctypes.c_float),
        ("kersInput", ctypes.c_float),
        ("autoShifterOn", ctypes.c_int32),
        ("rideHeight", ctypes.c_float * 2),
        ("turboBoost", ctypes.c_float),
        ("ballast", ctypes.c_float),
        ("airDensity", ctypes.c_float),
        ("airTemp", ctypes.c_float),
        ("roadTemp", ctypes.c_float),
        ("localAngularVel", ctypes.c_float * 3),
        ("finalFF", ctypes.c_float),
        ("performanceMeter", ctypes.c_float),
        ("engineBrake", ctypes.c_int32),
        ("ersRecoveryLevel", ctypes.c_int32),
        ("ersPowerLevel", ctypes.c_int32),
        ("ersHeatCharging", ctypes.c_int32),
        ("ersIsCharging", ctypes.c_int32),
        ("kersCurrentKJ", ctypes.c_float),
        ("drsAvailable", ctypes.c_int32),
        ("drsEnabled", ctypes.c_int32),
        ("brakeTemp", ctypes.c_float * 4),
        ("clutch", ctypes.c_float),
        ("tyreTempI", ctypes.c_float * 4),
        ("tyreTempM", ctypes.c_float * 4),
        ("tyreTempO", ctypes.c_float * 4),
        ("isAIControlled", ctypes.c_int32),
    ]


class _GraphicsPage(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packetId", ctypes.c_int32),
        ("status", ctypes.c_int32),        # 0=OFF 1=REPLAY 2=LIVE 3=PAUSE
        ("session", ctypes.c_int32),
        ("currentTime", ctypes.c_wchar * 15),
        ("lastTime", ctypes.c_wchar * 15),
        ("bestTime", ctypes.c_wchar * 15),
        ("split", ctypes.c_wchar * 15),
        ("completedLaps", ctypes.c_int32),
        ("position", ctypes.c_int32),
        ("iCurrentTime", ctypes.c_int32),  # ms
        ("iLastTime", ctypes.c_int32),     # ms
        ("iBestTime", ctypes.c_int32),     # ms
        ("sessionTimeLeft", ctypes.c_float),
        ("distanceTraveled", ctypes.c_float),
        ("isInPit", ctypes.c_int32),
        ("currentSectorIndex", ctypes.c_int32),
        ("lastSectorTime", ctypes.c_int32),
        ("numberOfLaps", ctypes.c_int32),
        ("tyreCompound", ctypes.c_wchar * 33),
        ("replayTimeMultiplier", ctypes.c_float),
        ("normalizedCarPosition", ctypes.c_float),  # 0.0 ~ 1.0 spline position
        ("activeCars", ctypes.c_int32),
        ("carCoordinates", (ctypes.c_float * 3) * 60),
        ("carID", ctypes.c_int32 * 60),
        ("playerCarID", ctypes.c_int32),
        ("penaltyTime", ctypes.c_float),
        ("flag", ctypes.c_int32),
        ("penalty", ctypes.c_int32),
        ("idealLineOn", ctypes.c_int32),
        ("isInPitLane", ctypes.c_int32),
        ("surfaceGrip", ctypes.c_float),
        ("mandatoryPitDone", ctypes.c_int32),
        ("windSpeed", ctypes.c_float),
        ("windDirection", ctypes.c_float),
        ("isSetupMenuVisible", ctypes.c_int32),
        ("mainDisplayIndex", ctypes.c_int32),
        ("secondaryDisplayIndex", ctypes.c_int32),
        ("TC", ctypes.c_int32),
        ("TCCut", ctypes.c_int32),
        ("EngineMap", ctypes.c_int32),
        ("ABS", ctypes.c_int32),
        ("fuelXLap", ctypes.c_float),
        ("rainLights", ctypes.c_int32),
        ("flashingLights", ctypes.c_int32),
        ("lightsStage", ctypes.c_int32),
        ("exhaustTemperature", ctypes.c_float),
        ("wiperLV", ctypes.c_int32),
        ("DriverStintTotalTimeLeft", ctypes.c_int32),
        ("DriverStintTimeLeft", ctypes.c_int32),
        ("rainTyres", ctypes.c_int32),
        ("sessionIndex", ctypes.c_int32),
        ("usedFuel", ctypes.c_float),
        ("deltaLapTime", ctypes.c_wchar * 15),
        ("iDeltaLapTime", ctypes.c_int32),
        ("estimatedLapTime", ctypes.c_wchar * 15),
        ("iEstimatedLapTime", ctypes.c_int32),
        ("isDeltaPositive", ctypes.c_int32),
        ("iSplit", ctypes.c_int32),
        ("isValidLap", ctypes.c_int32),
        ("fuelEstimatedLaps", ctypes.c_float),
        ("trackStatus", ctypes.c_wchar * 33),
        ("missingMandatoryPits", ctypes.c_int32),
        ("Clock", ctypes.c_float),
        ("directionLightsLeft", ctypes.c_int32),
        ("directionLightsRight", ctypes.c_int32),
    ]


class _StaticPage(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("smVersion", ctypes.c_wchar * 15),
        ("acVersion", ctypes.c_wchar * 15),
        ("numberOfSessions", ctypes.c_int32),
        ("numCars", ctypes.c_int32),
        ("carModel", ctypes.c_wchar * 33),
        ("track", ctypes.c_wchar * 33),
        ("playerName", ctypes.c_wchar * 33),
        ("playerSurname", ctypes.c_wchar * 33),
        ("playerNick", ctypes.c_wchar * 33),
        ("sectorCount", ctypes.c_int32),
    ]


@dataclass
class PhysicsSnapshot:
    packet_id: int
    throttle: float        # 0.0 ~ 1.0
    brake: float           # 0.0 ~ 1.0
    gear: int              # -1 = R, 0 = N, 1 = 1檔（已從 raw 值換算）
    rpm: int
    steer_angle: float     # -1.0 ~ 1.0
    speed_kmh: float
    acc_lat: float         # 橫向 G（accG[0]）
    acc_lon: float         # 縱向 G（accG[2]）
    tyre_temp: tuple       # 胎心溫度 (FL, FR, RL, RR) °C
    tyre_pressure: tuple   # 胎壓 (FL, FR, RL, RR) psi


@dataclass
class GraphicsSnapshot:
    packet_id: int
    status: int            # 2 = LIVE
    session_type: int      # 0=practice 1=qualify 2=race 3=hotlap...
    completed_laps: int
    current_lap_time_ms: int
    last_lap_time_ms: int
    best_lap_time_ms: int
    spline_position: float
    is_valid_lap: bool
    is_in_pit: bool
    current_sector: int
    world_x: float         # 玩家車輛世界座標（carCoordinates，賽道地圖用）
    world_y: float


@dataclass
class StaticInfo:
    sm_version: str
    car_model: str
    track: str
    player_name: str
    sector_count: int


class SharedMemoryReader:
    """開啟三個 ACC 共享記憶體頁並提供 snapshot 讀取。"""

    def __init__(self):
        self._physics_mm = mmap.mmap(-1, ctypes.sizeof(_PhysicsPage),
                                     "Local\\acpmf_physics")
        self._graphics_mm = mmap.mmap(-1, ctypes.sizeof(_GraphicsPage),
                                      "Local\\acpmf_graphics")
        self._static_mm = mmap.mmap(-1, ctypes.sizeof(_StaticPage),
                                    "Local\\acpmf_static")

    def close(self) -> None:
        self._physics_mm.close()
        self._graphics_mm.close()
        self._static_mm.close()

    def is_acc_running(self) -> bool:
        return bool(self.read_static().sm_version)

    def read_physics(self) -> PhysicsSnapshot:
        page = _PhysicsPage.from_buffer_copy(self._physics_mm)
        return PhysicsSnapshot(
            packet_id=page.packetId,
            throttle=page.gas,
            brake=page.brake,
            gear=page.gear - 1,   # shared memory raw: 0=R 1=N 2=1檔 → 統一成 -1/0/1
            rpm=page.rpms,
            steer_angle=page.steerAngle,
            speed_kmh=page.speedKmh,
            acc_lat=page.accG[0],
            acc_lon=page.accG[2],
            tyre_temp=tuple(page.tyreCoreTemperature),
            tyre_pressure=tuple(page.wheelsPressure),
        )

    def read_graphics(self) -> GraphicsSnapshot:
        page = _GraphicsPage.from_buffer_copy(self._graphics_mm)
        # 玩家車輛座標：在 carID 陣列中找 playerCarID 的索引
        wx = wy = 0.0
        for i in range(min(page.activeCars, 60)):
            if page.carID[i] == page.playerCarID:
                # carCoordinates 為 [x, y, z]，y 是垂直軸 → 地圖用 x/z
                wx = page.carCoordinates[i][0]
                wy = page.carCoordinates[i][2]
                break
        return GraphicsSnapshot(
            packet_id=page.packetId,
            status=page.status,
            session_type=page.session,
            completed_laps=page.completedLaps,
            current_lap_time_ms=page.iCurrentTime,
            last_lap_time_ms=page.iLastTime,
            best_lap_time_ms=page.iBestTime,
            spline_position=page.normalizedCarPosition,
            is_valid_lap=page.isValidLap > 0,
            is_in_pit=page.isInPit > 0,
            current_sector=page.currentSectorIndex,
            world_x=wx,
            world_y=wy,
        )

    def read_static(self) -> StaticInfo:
        page = _StaticPage.from_buffer_copy(self._static_mm)
        return StaticInfo(
            sm_version=page.smVersion,
            car_model=page.carModel,
            track=page.track,
            player_name=(page.playerName + " " + page.playerSurname).strip(),
            sector_count=page.sectorCount,
        )
