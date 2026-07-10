"""彎道對照表：把 spline 位置翻譯成官方彎名。

對照表存於 data/tracks/<game>/<track>.json（每個遊戲的 spline 原點不同，分開存）。
沒有對照表的賽道優雅退化——回傳 None，呼叫端顯示「區段 #N」。
"""
from __future__ import annotations

import json
from pathlib import Path

_TRACKS_DIR = Path(__file__).resolve().parent.parent / "data" / "tracks"


def load_corners(game: str, track: str) -> list | None:
    """回傳彎道清單 [{numbers, name, start, end}, ...]，無對照表回傳 None。"""
    if not game or not track:
        return None
    # iRacing 的 track 名有空格（如 "okayama full"），檔名以底線代替
    slug = track.strip().lower().replace(" ", "_")
    path = _TRACKS_DIR / game / f"{slug}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)["corners"]


def corner_at(corners: list | None, spline: float) -> dict | None:
    """找出 spline 位置所在（或最近）的彎，距離超過賽道 3% 視為不在彎內。"""
    if not corners:
        return None
    for c in corners:
        if c["start"] <= spline <= c["end"]:
            return c
    nearest = min(corners,
                  key=lambda c: min(abs(spline - c["start"]), abs(spline - c["end"])))
    dist = min(abs(spline - nearest["start"]), abs(spline - nearest["end"]))
    return nearest if dist <= 0.03 else None


def annotate_zones(zones: list, corners: list | None) -> dict:
    """回傳 {zone.index: '彎名 (編號)'}，找不到的區段不在 dict 裡。"""
    names = {}
    for z in zones:
        mid = (z.start + z.apex) / 2 if hasattr(z, "apex") else z.start
        c = corner_at(corners, mid)
        if c:
            names[z.index] = f"{c['name']} ({c['numbers']})"
    return names
