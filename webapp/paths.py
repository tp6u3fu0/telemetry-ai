"""路徑解析：區分「唯讀 bundle 資源」與「可寫使用者資料」。

打包成 exe（PyInstaller onefile）後，程式碼與資料被解壓到唯讀的 _MEIPASS
暫存區（關閉即刪）。config.json / telemetry.sqlite3 若寫在那裡會每次啟動遺失。
因此可寫資料一律導向使用者資料夾。

開發模式（未凍結）維持原本行為——資源與資料都在專案根目錄，
不會讓既有的 data/telemetry.sqlite3 變孤兒。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_dir() -> Path:
    """唯讀資源根（打包資料如 static / 彎道對照表）。"""
    if is_frozen():
        return Path(sys._MEIPASS)          # type: ignore[attr-defined]
    return _PROJECT_ROOT


def user_data_dir() -> Path:
    """可寫使用者資料夾。打包後為 %LOCALAPPDATA%\\ACC-Telemetry，
    開發時維持專案根目錄（既有 config.json / data/ 原地沿用）。"""
    if not is_frozen():
        return _PROJECT_ROOT
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) / "ACC-Telemetry" if base else Path.home() / ".acc-telemetry"
    root.mkdir(parents=True, exist_ok=True)
    return root
