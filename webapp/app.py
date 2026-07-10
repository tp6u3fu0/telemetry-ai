"""遙測儀表板 Web server。

用法：
    uv run python -m webapp.app              # http://127.0.0.1:5000
    uv run python -m webapp.app --port 8080 --db data/telemetry.sqlite3
"""
from __future__ import annotations

import argparse
import os

import anthropic
import numpy as np
from flask import Flask, jsonify, request

from agent import coach
from analysis.compare import compare_laps, microsectors, summarize, tyre_summary
from analysis.corners import annotate_zones, load_corners
from analysis.loader import load_lap
from analysis.single import analyze_lap, summarize_single
from data_store.db import TelemetryDB

from .config import load_config, save_config
from .recording import service as recording_service

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["DB_PATH"] = "data/telemetry.sqlite3"


def _db() -> TelemetryDB:
    # SQLite 連線不能跨 thread，每個 request 開一個（單人本機工具，成本可忽略）
    return TelemetryDB(app.config["DB_PATH"])


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/sessions")
def api_sessions():
    db = _db()
    try:
        return jsonify([dict(s) for s in db.list_sessions()])
    finally:
        db.close()


@app.get("/api/laps/<int:session_id>")
def api_laps(session_id: int):
    db = _db()
    try:
        laps = [dict(l) for l in db.list_laps(session_id)]
        best = db.best_lap(session_id)
        return jsonify({"laps": laps,
                        "best_lap_id": best["lap_id"] if best else None})
    finally:
        db.close()


def _compare_with_context(db: TelemetryDB, lap_a: int, lap_b: int):
    """比較兩圈並附上 session 資訊與彎名對照。"""
    comp = compare_laps(load_lap(db, lap_a), load_lap(db, lap_b))
    session = db.conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?",
        (comp.lap_a.session_id,)).fetchone()
    corners = load_corners(session["game"] if session else "acc",
                           session["track"] if session else "")
    corner_names = annotate_zones(comp.zones, corners)
    return comp, corner_names, session


def _single_with_context(db: TelemetryDB, lap_id: int):
    trace = load_lap(db, lap_id)
    analysis = analyze_lap(trace)
    session = db.conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?",
        (trace.session_id,)).fetchone()
    corners = load_corners(session["game"] if session else "acc",
                           session["track"] if session else "")
    corner_names = annotate_zones(analysis.zones, corners)
    return analysis, corner_names, session


@app.get("/api/lap")
def api_lap():
    lap_id = request.args.get("id", type=int)
    if lap_id is None:
        return jsonify({"error": "需要 id"}), 400
    db = _db()
    try:
        try:
            a, corner_names, _ = _single_with_context(db, lap_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        def arr(x):
            v = np.round(np.asarray(x, dtype=float), 4)
            return [None if np.isnan(val) else float(val) for val in v]

        t = a.trace
        return jsonify({
            "lap": {"lap_id": lap_id, "label": t.label,
                    "lap_time_ms": t.lap_time_ms, "is_valid": t.is_valid},
            "grid_pct": arr(a.grid * 100),
            "speed": arr(a.ch["speed"]),
            "throttle": arr(a.ch["throttle"] * 100),
            "brake": arr(a.ch["brake"] * 100),
            "steering": arr(a.ch["steering"]),
            "gear": arr(a.ch["gear"]),
            "top_speed": round(a.top_speed),
            "min_speed": round(a.min_speed),
            "map_x": arr(a.ch["world_x"]) if "world_x" in a.ch else None,
            "map_y": arr(a.ch["world_y"]) if "world_y" in a.ch else None,
            "zones": [{
                "index": z.index,
                "corner": corner_names.get(z.index),
                "start_pct": round(z.start * 100, 1),
                "end_pct": round(z.end * 100, 1),
                "brake_on_pct": round(z.brake_on * 100, 2),
                "entry_speed": round(z.entry_speed),
                "min_speed": round(z.min_speed),
                "exit_speed": round(z.exit_speed),
            } for z in a.zones],
            "tyres": tyre_summary(a.ch),
            "summary": summarize_single(a, corner_names),
        })
    finally:
        db.close()


@app.get("/api/compare")
def api_compare():
    lap_a = request.args.get("a", type=int)
    lap_b = request.args.get("b", type=int)
    if lap_a is None or lap_b is None:
        return jsonify({"error": "需要 a 與 b 兩個 lap_id"}), 400
    db = _db()
    try:
        try:
            comp, corner_names, _ = _compare_with_context(db, lap_a, lap_b)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        def arr(x):
            # nan → null（JSON 不接受 NaN；uPlot 以 null 表示缺值）
            a = np.round(np.asarray(x, dtype=float), 4)
            return [None if np.isnan(v) else float(v) for v in a]

        return jsonify({
            "lap_a": {"lap_id": lap_a, "label": comp.lap_a.label,
                      "lap_time_ms": comp.lap_a.lap_time_ms},
            "lap_b": {"lap_id": lap_b, "label": comp.lap_b.label,
                      "lap_time_ms": comp.lap_b.lap_time_ms},
            "grid_pct": arr(comp.grid * 100),
            "speed_a": arr(comp.a["speed"]), "speed_b": arr(comp.b["speed"]),
            "throttle_a": arr(comp.a["throttle"] * 100),
            "throttle_b": arr(comp.b["throttle"] * 100),
            "brake_a": arr(comp.a["brake"] * 100),
            "brake_b": arr(comp.b["brake"] * 100),
            "gear_a": arr(comp.a["gear"]), "gear_b": arr(comp.b["gear"]),
            "steering_a": arr(comp.a["steering"]),
            "steering_b": arr(comp.b["steering"]),
            "delta_s": arr(comp.delta_ms / 1000.0),
            # 賽道地圖：優先用參考圈的座標（v2 資料才有）
            "map_x": arr(comp.a["world_x"]) if "world_x" in comp.a
                     else (arr(comp.b["world_x"]) if "world_x" in comp.b else None),
            "map_y": arr(comp.a["world_y"]) if "world_x" in comp.a
                     else (arr(comp.b["world_y"]) if "world_x" in comp.b else None),
            "microsectors": [{
                "start_pct": round(m["start"] * 100, 1),
                "end_pct": round(m["end"] * 100, 1),
                "delta_s": round(m["delta_ms"] / 1000.0, 3),
            } for m in microsectors(comp)],
            "tyres_a": tyre_summary(comp.a), "tyres_b": tyre_summary(comp.b),
            "total_delta_s": round(comp.total_delta_ms / 1000.0, 3),
            "zones": [{
                "index": z.index,
                "corner": corner_names.get(z.index),
                "start_pct": round(z.start * 100, 1),
                "end_pct": round(z.end * 100, 1),
                "brake_on_a_pct": None if np.isnan(z.brake_on_a) else round(z.brake_on_a * 100, 2),
                "brake_on_b_pct": None if np.isnan(z.brake_on_b) else round(z.brake_on_b * 100, 2),
                "min_speed_a": round(z.min_speed_a), "min_speed_b": round(z.min_speed_b),
                "exit_speed_a": round(z.exit_speed_a), "exit_speed_b": round(z.exit_speed_b),
                "time_lost_s": round(z.time_lost_ms / 1000.0, 3),
                "entry_loss_s": round(z.entry_loss_ms / 1000.0, 3),
                "exit_loss_s": round(z.exit_loss_ms / 1000.0, 3),
            } for z in comp.zones],
            "summary": summarize(comp, corner_names),
        })
    finally:
        db.close()


@app.post("/api/coach")
def api_coach():
    """AI 教練對話。body: {a, b?, messages}。b 省略 = 單圈分析。成功後自動存檔。"""
    if not coach.has_credentials():
        return jsonify({"error": "尚未設定 Claude API 金鑰。"
                                 "點左上角 ⚙ 設定即可，不需重啟。"}), 503
    body = request.get_json(silent=True) or {}
    lap_a, lap_b = body.get("a"), body.get("b")
    messages = body.get("messages") or []
    if not lap_a or not messages:
        return jsonify({"error": "需要 a 與 messages"}), 400

    db = _db()
    try:
        try:
            if lap_b:
                comp, corner_names, session = _compare_with_context(db, lap_a, lap_b)
                summary = summarize(comp, corner_names)
            else:
                analysis, corner_names, session = _single_with_context(db, lap_a)
                summary = summarize_single(analysis, corner_names)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        system_blocks = coach.build_context(
            summary=summary,
            track=session["track"] if session else "",
            car=session["car_model"] if session else "",
        )
        try:
            reply = coach.ask(system_blocks, messages)
        except anthropic.AuthenticationError:
            return jsonify({"error": "API 金鑰無效，請到 ⚙ 設定檢查。"}), 401
        except anthropic.RateLimitError:
            return jsonify({"error": "API 速率限制，稍後再試。"}), 429
        except anthropic.APIStatusError as exc:
            return jsonify({"error": f"Claude API 錯誤（{exc.status_code}）"}), 502
        except anthropic.APIConnectionError:
            return jsonify({"error": "無法連線到 Claude API，請檢查網路。"}), 502
        db.save_chat(lap_a, lap_b or 0,
                     messages + [{"role": "assistant", "content": reply}])
        return jsonify({"reply": reply})
    finally:
        db.close()


@app.get("/api/coach/history")
def api_coach_history():
    lap_a = request.args.get("a", type=int)
    lap_b = request.args.get("b", type=int) or 0
    if not lap_a:
        return jsonify({"error": "需要 a"}), 400
    db = _db()
    try:
        return jsonify({"messages": db.get_chat(lap_a, lap_b)})
    finally:
        db.close()


@app.delete("/api/coach/history")
def api_coach_history_delete():
    lap_a = request.args.get("a", type=int)
    lap_b = request.args.get("b", type=int) or 0
    if not lap_a:
        return jsonify({"error": "需要 a"}), 400
    db = _db()
    try:
        db.delete_chat(lap_a, lap_b)
        return jsonify({"ok": True})
    finally:
        db.close()


@app.delete("/api/sessions/<int:session_id>")
def api_delete_session(session_id: int):
    db = _db()
    try:
        db.delete_session(session_id)
        return jsonify({"ok": True})
    finally:
        db.close()


@app.post("/api/sessions/<int:session_id>/rename")
def api_rename_session(session_id: int):
    label = (request.get_json(silent=True) or {}).get("label", "")
    db = _db()
    try:
        db.rename_session(session_id, label)
        return jsonify({"ok": True})
    finally:
        db.close()


@app.get("/api/settings")
def api_get_settings():
    cfg = load_config()
    key = cfg.get("anthropic_api_key", "").strip()
    return jsonify({
        "coach_model": cfg.get("coach_model", "claude-sonnet-5"),
        "api_key_set": bool(key),
        "api_key_hint": f"…{key[-4:]}" if len(key) >= 8 else "",
        "env_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    })


@app.post("/api/settings")
def api_save_settings():
    body = request.get_json(silent=True) or {}
    updates = {}
    if "api_key" in body:                    # 空字串 = 清除金鑰
        updates["anthropic_api_key"] = str(body["api_key"]).strip()
    if "coach_model" in body:
        updates["coach_model"] = str(body["coach_model"]).strip()
    save_config(updates)
    return jsonify({"ok": True})


@app.post("/api/settings/test")
def api_test_settings():
    if not coach.has_credentials():
        return jsonify({"ok": False, "message": "尚未設定金鑰"})
    ok, message = coach.verify_key()
    return jsonify({"ok": ok, "message": message})


@app.post("/api/record/start")
def api_record_start():
    ok, msg = recording_service.start(app.config["DB_PATH"])
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)


@app.post("/api/record/stop")
def api_record_stop():
    ok, msg = recording_service.stop()
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)


@app.get("/api/record/status")
def api_record_status():
    return jsonify(recording_service.status)


def main() -> None:
    parser = argparse.ArgumentParser(description="ACC 遙測儀表板")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--db", default="data/telemetry.sqlite3")
    args = parser.parse_args()
    app.config["DB_PATH"] = args.db
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
