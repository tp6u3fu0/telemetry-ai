"""階段三 CLI：比較兩圈，輸出比較圖 + 文字摘要。

用法：
    python -m analysis.compare_laps                 # 最新 session：最快圈 vs 最近一圈
    python -m analysis.compare_laps 3 7             # 指定 lap_id（A=參考圈, B=比較圈）
    python -m analysis.compare_laps --session 2     # 指定 session 的最快圈 vs 最近一圈
    python -m analysis.compare_laps 3 7 --out my.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from data_store.db import TelemetryDB

from .compare import compare_laps, summarize
from .loader import load_lap
from .plot import plot_comparison


def _auto_pick(db: TelemetryDB, session_id) -> tuple:
    """挑參考圈（最快有效完整圈）與比較圈（該 session 最近的另一完整圈）。"""
    if session_id is None:
        sessions = db.list_sessions()
        candidates = [s["session_id"] for s in sessions if s["lap_count"] >= 2]
        if not candidates:
            raise SystemExit("找不到有 2 圈以上的 session，先多錄幾圈。")
        session_id = candidates[-1]
    best = db.best_lap(session_id)
    if best is None:
        raise SystemExit(f"session #{session_id} 沒有有效的完整圈。")
    others = [l for l in db.list_laps(session_id)
              if l["is_complete"] and l["lap_id"] != best["lap_id"]]
    if not others:
        raise SystemExit(f"session #{session_id} 除了最快圈外沒有其他完整圈。")
    return best["lap_id"], others[-1]["lap_id"]


def main() -> int:
    parser = argparse.ArgumentParser(description="兩圈遙測比較")
    parser.add_argument("lap_a", nargs="?", type=int, help="參考圈 lap_id（通常是最快圈）")
    parser.add_argument("lap_b", nargs="?", type=int, help="比較圈 lap_id")
    parser.add_argument("--session", type=int, help="自動挑圈時指定 session")
    parser.add_argument("--db", default="data/telemetry.sqlite3")
    parser.add_argument("--out", help="輸出 PNG 路徑（預設 data/compare_A_vs_B.png）")
    args = parser.parse_args()

    db = TelemetryDB(args.db)
    try:
        if args.lap_a is not None and args.lap_b is not None:
            lap_a_id, lap_b_id = args.lap_a, args.lap_b
        elif args.lap_a is None:
            lap_a_id, lap_b_id = _auto_pick(db, args.session)
            print(f"自動選圈：A=lap_id {lap_a_id}（最快圈）, B=lap_id {lap_b_id}")
        else:
            raise SystemExit("要嘛兩個 lap_id 都給，要嘛都不給（自動挑）。")

        lap_a = load_lap(db, lap_a_id)
        lap_b = load_lap(db, lap_b_id)
        comp = compare_laps(lap_a, lap_b)

        print()
        print(summarize(comp))

        out = args.out or f"data/compare_{lap_a_id}_vs_{lap_b_id}.png"
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        plot_comparison(comp, out)
        print(f"\n比較圖已輸出：{out}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
