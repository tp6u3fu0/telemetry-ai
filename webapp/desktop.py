"""桌面 app 入口：Flask 跑在背景 thread，pywebview 開原生視窗（WebView2）。

用法：
    uv run python -m webapp.desktop
    uv run python -m webapp.desktop --db data/telemetry.sqlite3

或直接雙擊專案根目錄的 Telemetry-AI.bat。
"""
from __future__ import annotations

import argparse
import threading

import webview
from werkzeug.serving import make_server

from .app import app
from .paths import user_data_dir


def main() -> None:
    # 預設 DB：開發時 = 專案根 data/；打包後 = %LOCALAPPDATA%\Telemetry-AI\data\
    default_db = str(user_data_dir() / "data" / "telemetry.sqlite3")
    parser = argparse.ArgumentParser(description="Telemetry AI 桌面版")
    parser.add_argument("--db", default=default_db)
    args = parser.parse_args()
    app.config["DB_PATH"] = args.db

    # port=0 讓 OS 挑空 port，避免與其他服務衝突
    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()

    webview.create_window(
        "Telemetry AI",
        f"http://127.0.0.1:{port}/",
        width=1360, height=900,
        background_color="#0d0d0d",
    )
    webview.start()          # 阻塞直到視窗關閉
    server.shutdown()


if __name__ == "__main__":
    main()
