"""ACC 遙測來源：包裝既有的 SharedMemoryReader 成統一 reader 介面。"""
from __future__ import annotations

from telemetry_listener.shared_memory import SharedMemoryReader


class ACCReader(SharedMemoryReader):
    game = "acc"
    display_name = "Assetto Corsa Competizione"

    def is_running(self) -> bool:
        return self.is_acc_running()
