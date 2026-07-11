"""F1 25 遙測來源：官方 UDP 遙測（預設 port 20777）→ 統一 snapshot。

遊戲內需開啟：設定 → 遙測設定 → UDP Telemetry = On（port 20777、格式 2025）。

封包格式（little-endian、無 padding；與 F1 24 同構）：
    header 29 bytes：packetFormat(H) gameYear(B) major(B) minor(B)
                     packetVersion(B) packetId(B) sessionUID(Q) sessionTime(f)
                     frameId(I) overallFrameId(I) playerCarIndex(B) secondary(B)
    id 0 Motion：60 bytes/車（worldPos xyz、velocity、G 值、yaw/pitch/roll）
    id 1 Session：trackLength(H)@33、sessionType(B)@35、trackId(b)@36
    id 2 LapData：57 bytes/車（圈時間、lapDistance、圈數、有效旗標...）
    id 6 CarTelemetry：60 bytes/車（速度、踏板、檔位、轉速、胎溫胎壓...）

背景 thread 收包更新最新狀態；read_* 從狀態組 snapshot。
測試可直接呼叫 parse_datagram() 餵合成封包，不需要 socket。
"""
from __future__ import annotations

import socket
import struct
import threading
import time

from data_store.opponents import OpponentSample
from telemetry_listener.shared_memory import (GraphicsSnapshot,
                                              PhysicsSnapshot, StaticInfo)

_HEADER = struct.Struct("<HBBBBBQfIIBB")          # 29 bytes
_PORT = 20777

# LapData 每車 57 bytes；只解需要的欄位（offset 相對每車起點）
_LAP_CAR_SIZE = 57
_TEL_CAR_SIZE = 60
_MOT_CAR_SIZE = 60

# F1 24/25 trackId → 賽道名
TRACK_IDS = {
    0: "Melbourne", 1: "Paul Ricard", 2: "Shanghai", 3: "Sakhir (Bahrain)",
    4: "Catalunya", 5: "Monaco", 6: "Montreal", 7: "Silverstone",
    8: "Hockenheim", 9: "Hungaroring", 10: "Spa", 11: "Monza",
    12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "COTA (Texas)",
    16: "Brazil (Interlagos)", 17: "Austria (Red Bull Ring)", 18: "Sochi",
    19: "Mexico", 20: "Baku", 21: "Sakhir Short", 22: "Silverstone Short",
    23: "Texas Short", 24: "Suzuka Short", 25: "Hanoi", 26: "Zandvoort",
    27: "Imola", 28: "Portimao", 29: "Jeddah", 30: "Miami",
    31: "Las Vegas", 32: "Qatar (Losail)",
}

# sessionType → 我們的慣例（0 practice / 1 quali / 2 race）
_SESSION_TYPE = {**{i: 0 for i in (1, 2, 3, 4)},
                 **{i: 1 for i in (5, 6, 7, 8, 9)},
                 **{i: 2 for i in (10, 11)}}

# driverStatus：0 in garage, 1 flying lap, 2 in lap, 3 out lap, 4 on track
_ON_TRACK_STATUSES = (1, 2, 3, 4)


class F1Reader:
    game = "f1_25"
    display_name = "F1 25"

    def __init__(self, port: int = _PORT, listen: bool = True):
        self._state: dict = {}          # telemetry / lap / session / motion / 全車陣列
        self._last_rx = 0.0
        self._frame = 0
        self._player_idx = 0
        self._stop = threading.Event()
        self._sock = None
        self._thread = None
        if listen:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("", port))
            self._sock.settimeout(0.5)
            self._thread = threading.Thread(target=self._listen, daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._sock:
            self._sock.close()

    def _listen(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self.parse_datagram(data)
            except Exception:
                pass  # 單一壞封包不影響串流

    # -- 封包解析 ------------------------------------------------------------

    def parse_datagram(self, data: bytes) -> None:
        if len(data) < _HEADER.size:
            return
        (fmt, _year, _maj, _min, _pver, packet_id, _uid, _stime,
         frame, _oframe, player_idx, _sec) = _HEADER.unpack_from(data, 0)
        if fmt < 2020:          # 不是 F1 系列封包
            return
        body = _HEADER.size
        self._frame = frame
        self._player_idx = player_idx
        if packet_id == 6 and len(data) >= body + 22 * _TEL_CAR_SIZE:
            cars = []
            for i in range(22):
                o = body + i * _TEL_CAR_SIZE
                (speed, throttle, steer, brake, _clutch, gear, rpm) = \
                    struct.unpack_from("<HfffBbH", data, o)
                cars.append({"speed": speed, "throttle": throttle,
                             "steer": steer, "brake": brake,
                             "gear": gear, "rpm": rpm})
            self._state["tel_cars"] = cars
            o = body + player_idx * _TEL_CAR_SIZE
            inner = struct.unpack_from("<4B", data, o + 34)   # 胎內溫 [RL,RR,FL,FR]
            press = struct.unpack_from("<4f", data, o + 40)   # 胎壓   [RL,RR,FL,FR]
            self._state["telemetry"] = {
                **cars[player_idx],
                "tyre_temp": (inner[2], inner[3], inner[0], inner[1]),   # → FL FR RL RR
                "tyre_press": (press[2], press[3], press[0], press[1]),
            }
        elif packet_id == 2 and len(data) >= body + 22 * _LAP_CAR_SIZE:
            cars = []
            for i in range(22):
                o = body + i * _LAP_CAR_SIZE
                last_ms, cur_ms = struct.unpack_from("<II", data, o)
                lap_dist = struct.unpack_from("<f", data, o + 20)[0]
                (_pos, lap_num, pit_status, _stops, _sector, invalid) = \
                    struct.unpack_from("<6B", data, o + 32)
                driver_status, result_status = struct.unpack_from("<BB", data, o + 44)
                cars.append({"last_ms": last_ms, "cur_ms": cur_ms,
                             "lap_dist": lap_dist, "lap_num": lap_num,
                             "pit": pit_status, "invalid": invalid,
                             "driver_status": driver_status,
                             "result_status": result_status})
            self._state["lap_cars"] = cars
            self._state["lap"] = cars[player_idx]
        elif packet_id == 4 and len(data) > body + 1:
            # Participants：車手名。F1 25 的 per-car struct 可能比 F1 24 大，
            # stride 由封包長度動態推算；名字欄位固定在每車 offset 7 起。
            n = data[body]
            stride = (len(data) - body - 1) // 22 if n else 0
            names = []
            for i in range(22):
                o = body + 1 + i * stride
                if stride < 40 or o + 7 + 32 > len(data):
                    names.append("")
                    continue
                raw = data[o + 7:o + 7 + 32]
                names.append(raw.split(b"\x00")[0].decode("utf-8", "replace"))
            self._state["names"] = names
        elif packet_id == 0 and len(data) >= body + (player_idx + 1) * _MOT_CAR_SIZE:
            o = body + player_idx * _MOT_CAR_SIZE
            wx, _wy, wz = struct.unpack_from("<fff", data, o)
            g_lat, g_lon = struct.unpack_from("<ff", data, o + 36)
            self._state["motion"] = {"x": wx, "y": wz,
                                     "g_lat": g_lat, "g_lon": g_lon}
        elif packet_id == 1 and len(data) >= body + 8:
            track_len = struct.unpack_from("<H", data, body + 4)[0]
            session_type = struct.unpack_from("<B", data, body + 6)[0]
            track_id = struct.unpack_from("<b", data, body + 7)[0]
            self._state["session"] = {
                "track_len": track_len,
                "session_type": _SESSION_TYPE.get(session_type, 0),
                "track": TRACK_IDS.get(track_id, f"F1 Track {track_id}"),
            }
        else:
            return
        self._last_rx = time.time()

    # -- reader 介面 ---------------------------------------------------------

    def is_running(self) -> bool:
        return (time.time() - self._last_rx) < 3.0

    def read_physics(self) -> PhysicsSnapshot:
        t = self._state.get("telemetry", {})
        m = self._state.get("motion", {})
        return PhysicsSnapshot(
            packet_id=self._frame,
            throttle=float(t.get("throttle", 0.0)),
            brake=float(t.get("brake", 0.0)),
            gear=int(t.get("gear", 0)),              # F1 原生 -1=R 0=N，同慣例
            rpm=int(t.get("rpm", 0)),
            steer_angle=float(t.get("steer", 0.0)),  # 原生就是 -1~1
            speed_kmh=float(t.get("speed", 0.0)),
            acc_lat=float(m.get("g_lat", 0.0)),
            acc_lon=float(m.get("g_lon", 0.0)),
            tyre_temp=tuple(float(v) for v in t.get("tyre_temp", (0,) * 4)),
            tyre_pressure=tuple(float(v) for v in t.get("tyre_press", (0,) * 4)),
        )

    def read_graphics(self) -> GraphicsSnapshot:
        lap = self._state.get("lap", {})
        ses = self._state.get("session", {})
        m = self._state.get("motion", {})
        track_len = ses.get("track_len", 0) or 0
        dist = lap.get("lap_dist", 0.0)
        if track_len > 0:
            spline = (dist % track_len) / track_len if dist >= 0 \
                else ((dist + track_len) % track_len) / track_len
        else:
            spline = 0.0
        live = self.is_running() and \
            lap.get("driver_status", 0) in _ON_TRACK_STATUSES
        return GraphicsSnapshot(
            packet_id=self._frame,
            status=2 if live else 0,
            session_type=ses.get("session_type", 0),
            completed_laps=max(0, int(lap.get("lap_num", 1)) - 1),
            current_lap_time_ms=int(lap.get("cur_ms", 0)),
            last_lap_time_ms=int(lap.get("last_ms", 0)),
            best_lap_time_ms=0,
            spline_position=float(spline),
            is_valid_lap=not lap.get("invalid", 0),
            is_in_pit=bool(lap.get("pit", 0)),
            current_sector=0,
            world_x=float(m.get("x", 0.0)),
            world_y=float(m.get("y", 0.0)),
        )

    def track_length_m(self) -> float:
        return float(self._state.get("session", {}).get("track_len", 0) or 0)

    def read_opponents(self) -> list:
        """全部對手車的取樣（F1 封包原生含全車遙測——完整保真度）。"""
        lap_cars = self._state.get("lap_cars")
        if not lap_cars:
            return []
        tel_cars = self._state.get("tel_cars", [])
        names = self._state.get("names", [])
        track_len = self.track_length_m()
        out = []
        for i, lap in enumerate(lap_cars):
            if i == self._player_idx:
                continue
            if lap.get("result_status") != 2:      # 2 = active
                continue
            tel = tel_cars[i] if i < len(tel_cars) else {}
            dist = lap.get("lap_dist", 0.0)
            spline = ((dist % track_len) / track_len) if track_len > 0 and dist >= 0 \
                else 0.0
            name = (names[i] if i < len(names) else "") or f"Car {i + 1}"
            out.append(OpponentSample(
                car_key=f"f1_{i}",
                name=name,
                spline=spline,
                laps=max(0, int(lap.get("lap_num", 1)) - 1),
                last_lap_ms=int(lap.get("last_ms", 0)),
                speed_kmh=float(tel.get("speed", 0)) or None,
                throttle=tel.get("throttle"),
                brake=tel.get("brake"),
                steer=tel.get("steer"),
                gear=tel.get("gear"),
                rpm=tel.get("rpm"),
                in_pit=bool(lap.get("pit", 0)),
                on_track=lap.get("driver_status", 0) in _ON_TRACK_STATUSES,
            ))
        return out

    def read_static(self) -> StaticInfo:
        ses = self._state.get("session", {})
        return StaticInfo(
            sm_version="f1udp",
            car_model="F1 25",
            track=ses.get("track", ""),
            player_name="",
            sector_count=3,
        )
