"""前端 smoke test：用 Flask in-process test_client 驗證 UI 契約與 API 形狀，
不需瀏覽器／port。守住「改壞前端最常見的三類回歸」：

  1. JS 依賴的關鍵 DOM 元素還在（分頁、5 張圖、拖曳槽、教練、專注畫面…）
  2. 分頁結構正確（4 個 tab-panel 的 data-panel）
  3. compare API 的對齊不變式：所有通道陣列長度 == grid_pct（同一位置對齊）

執行：uv run python tests/test_webapp.py
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_store.db import TelemetryDB              # noqa: E402
from webapp.app import app                         # noqa: E402

# app.js 透過 $()/getElementById 依賴、缺了就會壞的關鍵元素
CRITICAL_IDS = [
    # 三大視圖
    "home-view", "focus-view", "dashboard-view",
    # 首頁動作
    "record-btn", "record-status", "train-enter", "train-panel",
    "personal-bests", "session-cards",
    # 儀表板分頁
    "dash-tabs", "session-select", "driver-select", "lap-palette",
    # 對比拖曳槽
    "slot-a", "slot-b", "slot-a-content", "slot-b-content", "slot-swap",
    "compare-toast",
    # 5 張通道圖
    "chart-speed", "chart-pedal", "chart-steering", "chart-gear", "chart-delta",
    "track-map", "microsectors", "zones-table",
    # tiles
    "tile-a", "tile-b", "tile-delta", "tile-worst",
    # 教練
    "coach-messages", "coach-input", "coach-send",
    # 專注畫面
    "focus-cur-time", "focus-lapcount", "focus-primary", "focus-home",
    # 設定（多供應商）
    "setting-provider", "setting-api-key", "setting-model", "setting-base-url",
]
TAB_PANELS = ["overview", "channels", "zones", "coach"]
# app.js 不該被截斷——這些函式在才代表核心行為都在
JS_MARKERS = ["setupDashTabs", "resizeAllCharts", "readColors", "openMaxedCard"]


def seed_db(path):
    """兩圈合成遙測（spline 0→1，B 圈整體略慢），足以跑 compare。"""
    db = TelemetryDB(path)
    sid = db.create_session(track="Test Circuit", car_model="Test GT3",
                            player="Me", session_type=0, game="acc")

    # 三個煞車彎（spline 0.2 / 0.5 / 0.8 附近）：煞車→減速→重新加速
    corners = [0.2, 0.5, 0.8]

    def lap_points(offset_kmh):
        pts = []
        n = 300
        for i in range(n):
            s = i / (n - 1)
            # 離最近彎心的距離 → 彎內煞車、彎外全油門
            near = min(abs(s - c) for c in corners)
            in_corner = near < 0.06
            brake = max(0.0, 0.9 - near / 0.06 * 0.9) if in_corner else 0.0
            throttle = 0.0 if in_corner else 1.0
            speed = (90 if in_corner else 240) - offset_kmh - (40 if in_corner else 0) * (1 - near / 0.06)
            pts.append((
                i * 300,                       # t_ms
                s,                             # spline
                max(40, speed),                # speed_kmh
                throttle, brake,               # throttle, brake
                0.6 * math.sin(s * 6 * math.pi),  # steering
                3 if in_corner else 5, 8000,   # gear, rpm
                100 * math.cos(s * 2 * math.pi),  # world_x
                100 * math.sin(s * 2 * math.pi),  # world_y
            ))
        return pts

    a = db.save_lap(sid, 1, 90000, True, True, lap_points(0))
    b = db.save_lap(sid, 2, 90500, True, True, lap_points(3))
    db.close()
    return sid, a, b


def main() -> int:
    failures = []
    tmp = os.path.join(tempfile.mkdtemp(), "web.sqlite3")
    sid, lap_a, lap_b = seed_db(tmp)
    app.config["DB_PATH"] = tmp
    app.config["TESTING"] = True
    c = app.test_client()

    # -- 1. index.html：關鍵 DOM 元素契約 --
    html = c.get("/").get_data(as_text=True)
    for eid in CRITICAL_IDS:
        if f'id="{eid}"' not in html:
            failures.append(f"index.html 缺少關鍵元素 #{eid}")
    for panel in TAB_PANELS:
        if f'data-panel="{panel}"' not in html:
            failures.append(f"缺少分頁 panel: {panel}")

    # -- 2. 靜態資源 app.js 未被截斷 --
    js = c.get("/app.js").get_data(as_text=True)
    for marker in JS_MARKERS:
        if marker not in js:
            failures.append(f"app.js 缺少 {marker}（檔案截斷或函式被移除？）")

    # -- 3. /api/laps 形狀 --
    laps = c.get(f"/api/laps/{sid}").get_json()
    if not laps or "laps" not in laps or laps.get("best_lap_id") is None:
        failures.append(f"/api/laps 形狀錯: {laps}")
    elif len([l for l in laps["laps"] if l["is_complete"]]) != 2:
        failures.append("應有 2 圈完整圈")

    # -- 4. compare 對齊不變式：所有通道長度 == grid_pct --
    d = c.get(f"/api/compare?a={lap_a}&b={lap_b}").get_json()
    if "error" in (d or {}):
        failures.append(f"/api/compare 失敗: {d['error']}")
    else:
        n = len(d["grid_pct"])
        channels = ["speed_a", "speed_b", "throttle_a", "throttle_b",
                    "brake_a", "brake_b", "gear_a", "gear_b",
                    "steering_a", "steering_b", "delta_s"]
        for ch in channels:
            if len(d[ch]) != n:
                failures.append(f"對齊破損：{ch} 長度 {len(d[ch])} != grid {n}")
        if not d.get("zones"):
            failures.append("compare 應回傳至少一個煞車區段")
        if d.get("map_x") is None or len(d["map_x"]) != n:
            failures.append("賽道地圖座標缺失或長度不符")

    # -- 5. 其他首頁 API 不炸 --
    for url in ("/api/sessions", "/api/personal-bests", "/api/train/progress"):
        r = c.get(url)
        if r.status_code != 200:
            failures.append(f"{url} 回應 {r.status_code}")

    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        return 1
    print("PASS  (DOM 契約 + 分頁結構 + compare 對齊不變式 + API 形狀)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
