"""對手圈錄製：從 reader.read_opponents() 的多車取樣切圈存檔。

各遊戲能力不同，取樣欄位皆為 optional：
    F1 25    → 完整（速度/踏板/檔位/轉速）
    iRacing  → spline/檔位/轉速，速度由 spline 位移推導
對手圈以 driver 名標記存進 laps 表，走與玩家圈相同的分析管線。

時間軸：對手沒有可靠的「圈內時間」來源，以偵測到 spline 過線的
單調時鐘起算；圈速優先用遊戲回報的 last lap time，缺漏用經過時間。
取樣率限制在 ~10Hz（22 台車 × 50Hz 會塞爆資料庫，10Hz 對 delta 分析足夠）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .db import TelemetryDB

_MIN_POINTS = 40          # 少於這個點數的圈不存（雜訊/剛加入戰局）
_MIN_LAP_MS = 20_000
_MAX_LAP_MS = 15 * 60_000


@dataclass
class OpponentSample:
    car_key: str                    # 遊戲內唯一識別（carIdx 等）
    name: str
    spline: float                   # 0~1
    laps: int = 0                   # 已完成圈數（有就給，沒有給 0）
    last_lap_ms: int = 0            # 遊戲回報的上一圈圈速（可為 0）
    speed_kmh: float | None = None  # None = 由 spline 推導
    throttle: float | None = None
    brake: float | None = None
    steer: float | None = None
    gear: int | None = None
    rpm: int | None = None
    in_pit: bool = False
    on_track: bool = True


class OpponentTracker:
    def __init__(self, db: TelemetryDB, session_id: int,
                 track_length_m: float = 0.0, hz: float = 10.0):
        self.db = db
        self.session_id = session_id
        self.track_length_m = track_length_m
        self.min_dt = 1.0 / hz
        self.laps_saved = 0
        self._cars: dict = {}       # car_key -> state

    def process(self, samples: list, now: float) -> None:
        """now: 單調時鐘（秒）。取樣間隔由呼叫端決定，這裡再限流到 hz。"""
        for s in samples:
            if not s.on_track:
                continue
            st = self._cars.get(s.car_key)
            if st is None:
                st = self._cars[s.car_key] = {
                    "name": s.name, "points": [], "lap_start": None,
                    "prev_spline": s.spline, "prev_t": now, "last_t": 0.0,
                    "lap_no": 0, "pit_seen": s.in_pit,
                }
                continue    # 第一筆只建狀態（不知道圈從哪開始）
            st["name"] = s.name or st["name"]

            # 過線偵測：spline 從高處掉回低處
            if st["prev_spline"] > 0.8 and s.spline < 0.2:
                self._close_lap(st, s, now)
                st["lap_start"] = now
                st["points"] = []
                st["pit_seen"] = False
            st["prev_spline"] = s.spline

            if s.in_pit:
                st["pit_seen"] = True
            if st["lap_start"] is None:
                continue    # 還沒看到這台車的第一次過線，資料不完整不記
            if now - st["last_t"] < self.min_dt:
                continue

            speed = s.speed_kmh
            if speed is None and self.track_length_m > 0:
                dt = now - st["prev_t"]
                if 0 < dt < 2.0:
                    ds = (s.spline - st["prev_spline_kept"]
                          if "prev_spline_kept" in st else 0.0)
                    if ds < -0.5:
                        ds += 1.0   # 剛好跨線
                    speed = max(0.0, ds * self.track_length_m / dt * 3.6)
            st["prev_spline_kept"] = s.spline
            st["prev_t"] = now
            st["last_t"] = now

            st["points"].append((
                int((now - st["lap_start"]) * 1000),
                s.spline,
                speed,
                s.throttle, s.brake, s.steer, s.gear, s.rpm,
            ))
            st["lap_no"] = s.laps

    def _close_lap(self, st: dict, s: OpponentSample, now: float) -> None:
        points = st["points"]
        if st["lap_start"] is None or len(points) < _MIN_POINTS:
            return
        elapsed = int((now - st["lap_start"]) * 1000)
        reported = s.last_lap_ms or 0
        lap_time = reported if abs(reported - elapsed) < 5000 and reported else elapsed
        if not (_MIN_LAP_MS < lap_time < _MAX_LAP_MS):
            return
        self.db.save_lap(
            self.session_id,
            lap_number=st["lap_no"] or (self.laps_saved + 1),
            lap_time_ms=lap_time,
            is_valid=not st["pit_seen"],   # 對手無有效圈旗標，進過 pit 視為非代表圈
            is_complete=True,
            points=points,
            driver=st["name"],
        )
        self.laps_saved += 1
