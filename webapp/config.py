"""App 設定：存於使用者資料夾 config.json（勿提交金鑰）。

開發時為專案根目錄；打包後為 %LOCALAPPDATA%\\ACC-Telemetry（見 paths.py）——
否則金鑰會寫進唯讀的 _MEIPASS 暫存區、每次啟動遺失。
"""
from __future__ import annotations

import json

from .paths import user_data_dir

CONFIG_PATH = user_data_dir() / "config.json"

DEFAULTS = {
    "anthropic_api_key": "",
    "coach_model": "claude-sonnet-5",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass  # 設定檔壞掉就用預設值，存檔時會覆寫修復
    return cfg


def save_config(updates: dict) -> dict:
    cfg = load_config()
    cfg.update({k: v for k, v in updates.items() if k in DEFAULTS})
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg
