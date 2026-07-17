"""遙測儀表板 Web server。

用法：
    uv run python -m webapp.app              # http://127.0.0.1:5000
    uv run python -m webapp.app --port 8080 --db data/telemetry.sqlite3
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from flask import Flask, Response, jsonify, request, stream_with_context

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


@app.errorhandler(Exception)
def _json_errors(exc):
    # 任何未攔截例外都回 JSON（前端 fetchJSON 才不會拿到 HTML 錯誤頁而爆
    # "Unexpected token '<'"）。HTTP 例外（404 等）維持原樣。
    from werkzeug.exceptions import HTTPException
    if isinstance(exc, HTTPException):
        return exc
    import traceback
    traceback.print_exc()
    return jsonify({"error": f"伺服器錯誤：{exc}"}), 500


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
        return jsonify({"error": "尚未設定 AI 教練的 API 金鑰。"
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
        context = coach.build_context(
            summary=summary,
            track=session["track"] if session else "",
            car=session["car_model"] if session else "",
        )
    finally:
        db.close()

    # 串流回覆：逐字送出，前端邊收邊顯示（大幅改善「像卡住」的體感）。
    # 錯誤多發生在串流開始前（憑證/context），已在上面攔掉；串流中的例外
    # 以文字附加回傳，並在成功結束時存檔。
    db_path = app.config["DB_PATH"]

    def generate():
        collected = []
        try:
            for chunk in coach.ask_stream(context, messages):
                collected.append(chunk)
                yield chunk
        except Exception as exc:                     # noqa: BLE001
            # 各供應商例外型別不一，統一以文字回報（多發生在串流開始時）
            import traceback
            traceback.print_exc()
            msg = str(exc) or exc.__class__.__name__
            yield f"\n\n[教練錯誤：{msg[:200]}]"
            return
        reply = "".join(collected)
        if reply:
            d = TelemetryDB(db_path)
            try:
                d.save_chat(lap_a, lap_b or 0,
                            messages + [{"role": "assistant", "content": reply}])
            finally:
                d.close()

    return Response(stream_with_context(generate()),
                    mimetype="text/plain; charset=utf-8")


@app.post("/api/coach/report")
def api_coach_report():
    """結構化分析報告。body: {a, b?}。一次性回傳 JSON（非串流）。

    解析失敗時回 {report: null, raw: 全文}，前端以一般訊息顯示 fallback。
    """
    if not coach.has_credentials():
        return jsonify({"error": "尚未設定 AI 教練的 API 金鑰。"
                                 "點左上角 ⚙ 設定即可，不需重啟。"}), 503
    body = request.get_json(silent=True) or {}
    lap_a, lap_b = body.get("a"), body.get("b")
    if not lap_a:
        return jsonify({"error": "需要 a"}), 400

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
        context = coach.build_context(
            summary=summary,
            track=session["track"] if session else "",
            car=session["car_model"] if session else "",
        )
    finally:
        db.close()

    try:
        report, raw = coach.ask_report(context)
    except Exception as exc:                          # noqa: BLE001
        msg = str(exc) or exc.__class__.__name__
        return jsonify({"error": f"報告產生失敗：{msg[:200]}"}), 502
    return jsonify({"report": report, "raw": raw})


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


_KEY_FIELDS = {"anthropic": "anthropic_api_key", "openai": "openai_api_key",
               "google": "google_api_key", "local": "local_api_key"}


@app.get("/api/settings")
def api_get_settings():
    cfg = load_config()

    def hint(field):
        k = cfg.get(field, "").strip()
        return f"…{k[-4:]}" if len(k) >= 8 else ("已設定" if k else "")

    return jsonify({
        "coach_provider": cfg.get("coach_provider", "anthropic"),
        "coach_model": cfg.get("coach_model", "claude-sonnet-5"),
        "local_base_url": cfg.get("local_base_url", "http://localhost:11434/v1"),
        # 各供應商金鑰只回「是否已設定」與末四碼提示，不回明文
        "keys_set": {p: bool(cfg.get(f, "").strip())
                     for p, f in _KEY_FIELDS.items()},
        "key_hints": {p: hint(f) for p, f in _KEY_FIELDS.items()},
        "env_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    })


@app.post("/api/settings")
def api_save_settings():
    body = request.get_json(silent=True) or {}
    updates = {}
    if "coach_provider" in body:
        updates["coach_provider"] = str(body["coach_provider"]).strip()
    if "coach_model" in body:
        updates["coach_model"] = str(body["coach_model"]).strip()
    if "local_base_url" in body:
        updates["local_base_url"] = str(body["local_base_url"]).strip()
    # 金鑰：key_provider 指定要更新哪一家；空字串 = 清除
    kp = body.get("key_provider")
    if kp in _KEY_FIELDS and "api_key" in body:
        updates[_KEY_FIELDS[kp]] = str(body["api_key"]).strip()
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
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "record")
    resume = bool(body.get("resume", False))
    ok, msg = recording_service.start(app.config["DB_PATH"], mode=mode,
                                      resume=resume)
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)


@app.post("/api/train/target")
def api_train_target():
    ms = (request.get_json(silent=True) or {}).get("ms")
    if not ms:
        return jsonify({"ok": False, "message": "需要 ms"}), 400
    ok, msg = recording_service.set_target(int(ms))
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)


@app.get("/api/train/progress")
def api_train_progress():
    """首頁用：是否有暫停中的訓練可續傳（附精簡狀態供顯示）。"""
    from training.five55 import Five55
    db = _db()
    try:
        prog = db.get_training_progress()
    finally:
        db.close()
    if not prog:
        return jsonify({"exists": False})
    state = Five55.from_dict(prog["state"]).state()
    return jsonify({"exists": True, "game": prog["game"],
                    "track": prog["track"], "updated_at": prog["updated_at"],
                    "state": state})


@app.post("/api/train/discard")
def api_train_discard():
    """放棄暫停中的訓練（清掉插槽，下次從頭）。"""
    if recording_service.active:
        return jsonify({"ok": False, "message": "訓練進行中，請先停止"}), 409
    db = _db()
    try:
        db.clear_training_progress()
    finally:
        db.close()
    return jsonify({"ok": True, "message": "已放棄暫停的訓練"})


@app.get("/api/trainings")
def api_trainings():
    db = _db()
    try:
        return jsonify(db.list_trainings())
    finally:
        db.close()


@app.get("/api/personal-bests")
def api_personal_bests():
    db = _db()
    try:
        return jsonify([dict(r) for r in db.personal_bests()])
    finally:
        db.close()


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
