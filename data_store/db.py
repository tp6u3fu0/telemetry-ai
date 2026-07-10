"""SQLite 儲存層：sessions / laps / telemetry_points 三張表。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    track        TEXT,
    car_model    TEXT,
    player       TEXT,
    session_type INTEGER
);

CREATE TABLE IF NOT EXISTS laps (
    lap_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(session_id),
    lap_number  INTEGER NOT NULL,
    lap_time_ms INTEGER,            -- NULL = 未完成的圈
    is_valid    INTEGER NOT NULL DEFAULT 1,
    is_complete INTEGER NOT NULL DEFAULT 1,
    point_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS telemetry_points (
    lap_id    INTEGER NOT NULL REFERENCES laps(lap_id),
    t_ms      INTEGER NOT NULL,     -- 相對於圈開始的時間（來自遊戲內 iCurrentTime）
    spline    REAL,                 -- 0.0 ~ 1.0 賽道位置
    speed_kmh REAL,
    throttle  REAL,                 -- 0.0 ~ 1.0
    brake     REAL,                 -- 0.0 ~ 1.0
    steering  REAL,                 -- -1.0 ~ 1.0
    gear      INTEGER,              -- -1 = R, 0 = N
    rpm       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_points_lap ON telemetry_points(lap_id, t_ms);
CREATE INDEX IF NOT EXISTS idx_laps_session ON laps(session_id, lap_number);

CREATE TABLE IF NOT EXISTS coach_chats (
    lap_a      INTEGER NOT NULL,
    lap_b      INTEGER NOT NULL DEFAULT 0,   -- 0 = 單圈分析
    updated_at TEXT NOT NULL,
    messages   TEXT NOT NULL,                -- JSON: [{role, content}, ...]
    PRIMARY KEY (lap_a, lap_b)
);
"""

# telemetry_points 的完整欄位順序（save_lap 的 points tuple 依此排列，
# 短 tuple 自動以 NULL 補齊——確保舊格式資料與測試相容）
POINT_COLUMNS = [
    "t_ms", "spline", "speed_kmh", "throttle", "brake", "steering", "gear", "rpm",
    # v2 新增通道（賽道地圖 / 摩擦圓 / 胎溫胎壓）
    "world_x", "world_y", "acc_lat", "acc_lon",
    "tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr",
    "tyre_press_fl", "tyre_press_fr", "tyre_press_rl", "tyre_press_rr",
]
_V2_COLUMNS = POINT_COLUMNS[8:]


class TelemetryDB:
    def __init__(self, path: str = "data/telemetry.sqlite3"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """為既有資料庫補上新欄位。"""
        existing = {r["name"] for r in
                    self.conn.execute("PRAGMA table_info(telemetry_points)")}
        for col in _V2_COLUMNS:
            if col not in existing:
                self.conn.execute(
                    f"ALTER TABLE telemetry_points ADD COLUMN {col} REAL")
        session_cols = {r["name"] for r in
                        self.conn.execute("PRAGMA table_info(sessions)")}
        if "label" not in session_cols:  # 使用者自訂名稱（session 管理功能）
            self.conn.execute("ALTER TABLE sessions ADD COLUMN label TEXT")
        if "game" not in session_cols:   # 多遊戲支援（acc / f1_25 / iracing...）
            self.conn.execute(
                "ALTER TABLE sessions ADD COLUMN game TEXT DEFAULT 'acc'")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- 寫入 ---------------------------------------------------------------

    def create_session(self, track: str, car_model: str, player: str,
                       session_type: int, game: str = "acc") -> int:
        cur = self.conn.execute(
            "INSERT INTO sessions (started_at, track, car_model, player, "
            "session_type, game) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), track, car_model,
             player, session_type, game))
        self.conn.commit()
        return cur.lastrowid

    def save_lap(self, session_id: int, lap_number: int, lap_time_ms,
                 is_valid: bool, is_complete: bool, points: list) -> int:
        """points: list of tuples，欄位順序見 POINT_COLUMNS；短 tuple 以 NULL 補齊。"""
        cur = self.conn.execute(
            "INSERT INTO laps (session_id, lap_number, lap_time_ms, is_valid, "
            "is_complete, point_count) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, lap_number, lap_time_ms, int(is_valid),
             int(is_complete), len(points)))
        lap_id = cur.lastrowid
        width = len(POINT_COLUMNS)
        cols = ", ".join(POINT_COLUMNS)
        marks = ", ".join("?" * (width + 1))
        self.conn.executemany(
            f"INSERT INTO telemetry_points (lap_id, {cols}) VALUES ({marks})",
            [(lap_id, *p, *([None] * (width - len(p)))) for p in points])
        self.conn.commit()
        return lap_id

    # -- 查詢 ---------------------------------------------------------------

    def list_sessions(self) -> list:
        return self.conn.execute(
            "SELECT s.*, COUNT(l.lap_id) AS lap_count "
            "FROM sessions s LEFT JOIN laps l ON l.session_id = s.session_id "
            "GROUP BY s.session_id ORDER BY s.session_id").fetchall()

    def list_laps(self, session_id: int) -> list:
        return self.conn.execute(
            "SELECT * FROM laps WHERE session_id = ? ORDER BY lap_number",
            (session_id,)).fetchall()

    def get_lap(self, lap_id: int):
        return self.conn.execute(
            "SELECT * FROM laps WHERE lap_id = ?", (lap_id,)).fetchone()

    def get_lap_points(self, lap_id: int) -> list:
        return self.conn.execute(
            "SELECT * FROM telemetry_points WHERE lap_id = ? ORDER BY t_ms",
            (lap_id,)).fetchall()

    def rename_session(self, session_id: int, label: str) -> None:
        self.conn.execute("UPDATE sessions SET label = ? WHERE session_id = ?",
                          (label.strip() or None, session_id))
        self.conn.commit()

    def delete_session(self, session_id: int) -> None:
        self.conn.execute(
            "DELETE FROM telemetry_points WHERE lap_id IN "
            "(SELECT lap_id FROM laps WHERE session_id = ?)", (session_id,))
        self.conn.execute("DELETE FROM laps WHERE session_id = ?", (session_id,))
        self.conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        self.conn.commit()

    # -- AI 教練對話 --------------------------------------------------------

    def get_chat(self, lap_a: int, lap_b: int = 0) -> list:
        row = self.conn.execute(
            "SELECT messages FROM coach_chats WHERE lap_a = ? AND lap_b = ?",
            (lap_a, lap_b or 0)).fetchone()
        return json.loads(row["messages"]) if row else []

    def save_chat(self, lap_a: int, lap_b: int, messages: list) -> None:
        self.conn.execute(
            "INSERT INTO coach_chats (lap_a, lap_b, updated_at, messages) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(lap_a, lap_b) DO UPDATE SET "
            "updated_at = excluded.updated_at, messages = excluded.messages",
            (lap_a, lap_b or 0, datetime.now().isoformat(timespec="seconds"),
             json.dumps(messages, ensure_ascii=False)))
        self.conn.commit()

    def delete_chat(self, lap_a: int, lap_b: int = 0) -> None:
        self.conn.execute(
            "DELETE FROM coach_chats WHERE lap_a = ? AND lap_b = ?",
            (lap_a, lap_b or 0))
        self.conn.commit()

    def best_lap(self, session_id: int):
        """該 session 最快的有效完整圈。"""
        return self.conn.execute(
            "SELECT * FROM laps WHERE session_id = ? AND is_valid = 1 "
            "AND is_complete = 1 AND lap_time_ms IS NOT NULL "
            "ORDER BY lap_time_ms LIMIT 1", (session_id,)).fetchone()
