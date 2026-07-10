"""遙測來源抽象：每個遊戲一個 reader，全部輸出相同的 snapshot 型別。

Reader 介面（duck typing，型別沿用 telemetry_listener.shared_memory 的 dataclass）：
    reader.game            -> str           ("acc" / "iracing" / ...)
    reader.display_name    -> str           (UI 顯示用)
    reader.is_running()    -> bool          (遊戲開著且資料可讀)
    reader.read_physics()  -> PhysicsSnapshot
    reader.read_graphics() -> GraphicsSnapshot   (status == 2 表示在賽道上 LIVE)
    reader.read_static()   -> StaticInfo
    reader.close()
"""
from __future__ import annotations

from .acc import ACCReader
from .iracing import IRacingReader

ALL_SOURCES = [ACCReader, IRacingReader]


def open_all() -> list:
    """開啟所有來源的 reader（各自處理遊戲未執行的情況）。"""
    readers = []
    for cls in ALL_SOURCES:
        try:
            readers.append(cls())
        except Exception:
            pass  # 某個來源初始化失敗（例如缺依賴）不影響其他遊戲
    return readers


def detect_live(readers: list):
    """回傳目前「在賽道上」的 reader，都沒有則回傳 None。"""
    for r in readers:
        try:
            if r.is_running() and r.read_graphics().status == 2:
                return r
        except Exception:
            continue
    return None
