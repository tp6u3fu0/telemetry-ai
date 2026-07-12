"""ACC Broadcasting API UDP client。

負責 handshake（registration）、心跳維持與封包分派。
使用 callback 模式：呼叫端註冊 on_* handler，client 在背景 thread 收包並回呼。
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional

from . import protocol
from .protocol import InboundMessage


class BroadcastClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9000,
                 display_name: str = "Telemetry AI Agent",
                 connection_password: str = "asd",
                 command_password: str = "",
                 update_interval_ms: int = 100):
        self.host = host
        self.port = port
        self.display_name = display_name
        self.connection_password = connection_password
        self.command_password = command_password
        self.update_interval_ms = update_interval_ms

        self.connection_id: Optional[int] = None
        self.connected = False
        self.last_error = ""

        # callbacks: fn(msg) -> None
        self.on_registration: Optional[Callable] = None
        self.on_realtime_update: Optional[Callable] = None
        self.on_realtime_car_update: Optional[Callable] = None
        self.on_entry_list: Optional[Callable] = None
        self.on_entry_list_car: Optional[Callable] = None
        self.on_track_data: Optional[Callable] = None
        self.on_broadcasting_event: Optional[Callable] = None

        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(1.0)
        self._sock.connect((self.host, self.port))
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        if self._sock:
            if self.connection_id is not None:
                try:
                    self._sock.send(protocol.encode_unregister_request(self.connection_id))
                except OSError:
                    pass
            self._sock.close()
            self._sock = None
        self.connected = False
        self.connection_id = None

    def _send_register(self) -> None:
        self._sock.send(protocol.encode_register_request(
            self.display_name, self.connection_password,
            self.update_interval_ms, self.command_password))

    def _run(self) -> None:
        self._send_register()
        last_register = time.monotonic()
        while not self._stop.is_set():
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                # 尚未成功註冊就定期重送（ACC 可能還沒開、或還沒進 session）
                if not self.connected and time.monotonic() - last_register > 3.0:
                    try:
                        self._send_register()
                    except OSError:
                        pass
                    last_register = time.monotonic()
                continue
            except OSError:
                break
            self._dispatch(data)

    def _dispatch(self, data: bytes) -> None:
        try:
            msg_type, msg = protocol.parse_message(data)
        except Exception as exc:  # 解析失敗不應讓收包 thread 死掉
            self.last_error = f"parse error: {exc!r} (len={len(data)})"
            return
        if msg_type is None:
            return

        if msg_type == InboundMessage.REGISTRATION_RESULT:
            if msg.success:
                self.connection_id = msg.connection_id
                self.connected = True
                # 註冊成功後主動要 entry list 與 track data
                self._sock.send(protocol.encode_request_entry_list(self.connection_id))
                self._sock.send(protocol.encode_request_track_data(self.connection_id))
            else:
                self.last_error = msg.error_message
            if self.on_registration:
                self.on_registration(msg)
        elif msg_type == InboundMessage.REALTIME_UPDATE:
            if self.on_realtime_update:
                self.on_realtime_update(msg)
        elif msg_type == InboundMessage.REALTIME_CAR_UPDATE:
            if self.on_realtime_car_update:
                self.on_realtime_car_update(msg)
        elif msg_type == InboundMessage.ENTRY_LIST:
            if self.on_entry_list:
                self.on_entry_list(msg)
        elif msg_type == InboundMessage.ENTRY_LIST_CAR:
            if self.on_entry_list_car:
                self.on_entry_list_car(msg)
        elif msg_type == InboundMessage.TRACK_DATA:
            if self.on_track_data:
                self.on_track_data(msg)
        elif msg_type == InboundMessage.BROADCASTING_EVENT:
            if self.on_broadcasting_event:
                self.on_broadcasting_event(msg)
