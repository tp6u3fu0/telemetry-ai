"""App 設定：存於專案根目錄 config.json（已加入 .gitignore，勿提交金鑰）。"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

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
