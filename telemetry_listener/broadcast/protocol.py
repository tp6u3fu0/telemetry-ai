"""ACC Broadcasting API 二進位協議的編碼與解析。

協議格式（little-endian）：
- string  = uint16 長度 + UTF-8 bytes
- 每個 UDP datagram 的第一個 byte 是 message type

Outbound (client -> ACC):
    1  = REGISTER_COMMAND_APPLICATION
    9  = UNREGISTER_COMMAND_APPLICATION
    10 = REQUEST_ENTRY_LIST
    11 = REQUEST_TRACK_DATA

Inbound (ACC -> client):
    1 = REGISTRATION_RESULT
    2 = REALTIME_UPDATE
    3 = REALTIME_CAR_UPDATE
    4 = ENTRY_LIST
    5 = TRACK_DATA
    6 = ENTRY_LIST_CAR
    7 = BROADCASTING_EVENT
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

PROTOCOL_VERSION = 4


class OutboundMessage(IntEnum):
    REGISTER_COMMAND_APPLICATION = 1
    UNREGISTER_COMMAND_APPLICATION = 9
    REQUEST_ENTRY_LIST = 10
    REQUEST_TRACK_DATA = 11


class InboundMessage(IntEnum):
    REGISTRATION_RESULT = 1
    REALTIME_UPDATE = 2
    REALTIME_CAR_UPDATE = 3
    ENTRY_LIST = 4
    TRACK_DATA = 5
    ENTRY_LIST_CAR = 6
    BROADCASTING_EVENT = 7


class SessionType(IntEnum):
    PRACTICE = 0
    QUALIFYING = 4
    SUPERPOLE = 9
    RACE = 10
    HOTLAP = 11
    HOTSTINT = 12
    HOTLAP_SUPERPOLE = 13
    REPLAY = 14


class CarLocation(IntEnum):
    NONE = 0
    TRACK = 1
    PITLANE = 2
    PIT_ENTRY = 3
    PIT_EXIT = 4


class BinaryReader:
    """循序讀取 little-endian 二進位資料。"""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def _read(self, fmt: str):
        value = struct.unpack_from("<" + fmt, self._data, self._pos)[0]
        self._pos += struct.calcsize(fmt)
        return value

    def u8(self) -> int:
        return self._read("B")

    def u16(self) -> int:
        return self._read("H")

    def i32(self) -> int:
        return self._read("i")

    def f32(self) -> float:
        return self._read("f")

    def string(self) -> str:
        length = self.u16()
        raw = self._data[self._pos:self._pos + length]
        self._pos += length
        return raw.decode("utf-8", errors="replace")

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos


def _write_string(parts: list, text: str) -> None:
    encoded = text.encode("utf-8")
    parts.append(struct.pack("<H", len(encoded)))
    parts.append(encoded)


def encode_register_request(display_name: str, connection_password: str,
                            update_interval_ms: int, command_password: str = "") -> bytes:
    parts = [struct.pack("<BB", OutboundMessage.REGISTER_COMMAND_APPLICATION, PROTOCOL_VERSION)]
    _write_string(parts, display_name)
    _write_string(parts, connection_password)
    parts.append(struct.pack("<i", update_interval_ms))
    _write_string(parts, command_password)
    return b"".join(parts)


def encode_unregister_request(connection_id: int) -> bytes:
    return struct.pack("<Bi", OutboundMessage.UNREGISTER_COMMAND_APPLICATION, connection_id)


def encode_request_entry_list(connection_id: int) -> bytes:
    return struct.pack("<Bi", OutboundMessage.REQUEST_ENTRY_LIST, connection_id)


def encode_request_track_data(connection_id: int) -> bytes:
    return struct.pack("<Bi", OutboundMessage.REQUEST_TRACK_DATA, connection_id)


# ---------------------------------------------------------------------------
# Inbound message dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RegistrationResult:
    connection_id: int
    success: bool
    read_only: bool
    error_message: str


@dataclass
class LapInfo:
    laptime_ms: Optional[int]          # None = 尚無圈速（封包內以 int32 max 表示）
    car_index: int
    driver_index: int
    splits_ms: list                    # 各 sector 時間，None 表示尚無
    is_invalid: bool
    is_valid_for_best: bool
    is_outlap: bool
    is_inlap: bool


@dataclass
class RealtimeUpdate:
    event_index: int
    session_index: int
    session_type: int
    phase: int
    session_time_ms: float
    session_end_time_ms: float
    focused_car_index: int
    active_camera_set: str
    active_camera: str
    current_hud_page: str
    is_replay_playing: bool
    time_of_day_ms: float
    ambient_temp: int
    track_temp: int
    clouds: int
    rain_level: int
    wetness: int
    best_session_lap: LapInfo


@dataclass
class RealtimeCarUpdate:
    car_index: int
    driver_index: int
    driver_count: int
    gear: int                # -1 = R, 0 = N, 1+ = 前進檔（換算方式待遊戲內核對，見 parser 註解）
    world_pos_x: float
    world_pos_y: float
    yaw: float
    car_location: int        # CarLocation
    speed_kmh: int
    position: int
    cup_position: int
    track_position: int
    spline_position: float   # 0.0 ~ 1.0
    laps: int
    delta_ms: int
    best_session_lap: LapInfo
    last_lap: LapInfo
    current_lap: LapInfo


@dataclass
class DriverInfo:
    first_name: str
    last_name: str
    short_name: str
    category: int
    nationality: int


@dataclass
class CarInfo:
    car_index: int
    car_model_type: int
    team_name: str
    race_number: int
    cup_category: int
    current_driver_index: int
    drivers: list = field(default_factory=list)


@dataclass
class EntryList:
    connection_id: int
    car_indexes: list


@dataclass
class TrackData:
    connection_id: int
    track_name: str
    track_id: int
    track_meters: int
    camera_sets: dict
    hud_pages: list


@dataclass
class BroadcastingEvent:
    event_type: int
    message: str
    time_ms: int
    car_id: int


_INT32_MAX = 2147483647


def _parse_lap(r: BinaryReader) -> LapInfo:
    laptime = r.i32()
    car_index = r.u16()
    driver_index = r.u16()
    splits = [r.i32() for _ in range(r.u8())]
    is_invalid = r.u8() > 0
    is_valid_for_best = r.u8() > 0
    is_outlap = r.u8() > 0
    is_inlap = r.u8() > 0
    to_none = lambda v: None if v == _INT32_MAX else v
    return LapInfo(
        laptime_ms=to_none(laptime),
        car_index=car_index,
        driver_index=driver_index,
        splits_ms=[to_none(s) for s in splits],
        is_invalid=is_invalid,
        is_valid_for_best=is_valid_for_best,
        is_outlap=is_outlap,
        is_inlap=is_inlap,
    )


def _parse_registration_result(r: BinaryReader) -> RegistrationResult:
    return RegistrationResult(
        connection_id=r.i32(),
        success=r.u8() > 0,
        read_only=r.u8() == 0,
        error_message=r.string(),
    )


def _parse_realtime_update(r: BinaryReader) -> RealtimeUpdate:
    event_index = r.u16()
    session_index = r.u16()
    session_type = r.u8()
    phase = r.u8()
    session_time = r.f32()
    session_end_time = r.f32()
    focused_car = r.i32()
    camera_set = r.string()
    camera = r.string()
    hud_page = r.string()
    is_replay = r.u8() > 0
    if is_replay:
        r.f32()  # replay session time
        r.f32()  # replay remaining time
    return RealtimeUpdate(
        event_index=event_index,
        session_index=session_index,
        session_type=session_type,
        phase=phase,
        session_time_ms=session_time,
        session_end_time_ms=session_end_time,
        focused_car_index=focused_car,
        active_camera_set=camera_set,
        active_camera=camera,
        current_hud_page=hud_page,
        is_replay_playing=is_replay,
        time_of_day_ms=r.f32(),
        ambient_temp=r.u8(),
        track_temp=r.u8(),
        clouds=r.u8(),
        rain_level=r.u8(),
        wetness=r.u8(),
        best_session_lap=_parse_lap(r),
    )


def _parse_realtime_car_update(r: BinaryReader) -> RealtimeCarUpdate:
    car_index = r.u16()
    driver_index = r.u16()
    driver_count = r.u8()
    # 官方 C# SDK：Gear = ReadByte() - 2（R=-1, N=0）。此換算需在遊戲內核對一次。
    gear = r.u8() - 2
    return RealtimeCarUpdate(
        car_index=car_index,
        driver_index=driver_index,
        driver_count=driver_count,
        gear=gear,
        world_pos_x=r.f32(),
        world_pos_y=r.f32(),
        yaw=r.f32(),
        car_location=r.u8(),
        speed_kmh=r.u16(),
        position=r.u16(),
        cup_position=r.u16(),
        track_position=r.u16(),
        spline_position=r.f32(),
        laps=r.u16(),
        delta_ms=r.i32(),
        best_session_lap=_parse_lap(r),
        last_lap=_parse_lap(r),
        current_lap=_parse_lap(r),
    )


def _parse_entry_list(r: BinaryReader) -> EntryList:
    connection_id = r.i32()
    count = r.u16()
    return EntryList(connection_id=connection_id,
                     car_indexes=[r.u16() for _ in range(count)])


def _parse_entry_list_car(r: BinaryReader) -> CarInfo:
    car = CarInfo(
        car_index=r.u16(),
        car_model_type=r.u8(),
        team_name=r.string(),
        race_number=r.i32(),
        cup_category=r.u8(),
        current_driver_index=r.u8(),
    )
    for _ in range(r.u8()):
        car.drivers.append(DriverInfo(
            first_name=r.string(),
            last_name=r.string(),
            short_name=r.string(),
            category=r.u8(),
            nationality=r.u16(),
        ))
    return car


def _parse_track_data(r: BinaryReader) -> TrackData:
    connection_id = r.i32()
    track_name = r.string()
    track_id = r.i32()
    track_meters = r.i32()
    camera_sets = {}
    for _ in range(r.u8()):
        set_name = r.string()
        camera_sets[set_name] = [r.string() for _ in range(r.u8())]
    hud_pages = [r.string() for _ in range(r.u8())]
    return TrackData(
        connection_id=connection_id,
        track_name=track_name,
        track_id=track_id,
        track_meters=track_meters,
        camera_sets=camera_sets,
        hud_pages=hud_pages,
    )


def _parse_broadcasting_event(r: BinaryReader) -> BroadcastingEvent:
    return BroadcastingEvent(
        event_type=r.u8(),
        message=r.string(),
        time_ms=r.i32(),
        car_id=r.i32(),
    )


_PARSERS = {
    InboundMessage.REGISTRATION_RESULT: _parse_registration_result,
    InboundMessage.REALTIME_UPDATE: _parse_realtime_update,
    InboundMessage.REALTIME_CAR_UPDATE: _parse_realtime_car_update,
    InboundMessage.ENTRY_LIST: _parse_entry_list,
    InboundMessage.ENTRY_LIST_CAR: _parse_entry_list_car,
    InboundMessage.TRACK_DATA: _parse_track_data,
    InboundMessage.BROADCASTING_EVENT: _parse_broadcasting_event,
}


def parse_message(data: bytes):
    """解析一個完整 UDP datagram，回傳 (InboundMessage, dataclass)。

    未知的 message type 回傳 (None, None)。
    """
    if not data:
        return None, None
    reader = BinaryReader(data)
    msg_type = reader.u8()
    try:
        msg_type = InboundMessage(msg_type)
    except ValueError:
        return None, None
    return msg_type, _PARSERS[msg_type](reader)
