"""階段二驗收工具：列出 session / 圈次，並可撈出某一圈的完整逐點資料。

用法：
    python -m data_store.inspect                       # 列出所有 session 與圈次
    python -m data_store.inspect --lap-id 3            # 顯示該圈摘要與前後幾點
    python -m data_store.inspect --lap-id 3 --csv out.csv   # 整圈匯出 CSV
"""
from __future__ import annotations

import argparse
import csv
import sys

from telemetry_listener.live_console import format_laptime

from .db import TelemetryDB


def show_overview(db: TelemetryDB) -> None:
    sessions = db.list_sessions()
    if not sessions:
        print("資料庫是空的，先用 python -m data_store.record 錄幾圈。")
        return
    for s in sessions:
        print(f"session #{s['session_id']}  {s['started_at']}  "
              f"{s['track']} / {s['car_model']}  ({s['lap_count']} laps)")
        for lap in db.list_laps(s["session_id"]):
            flags = []
            if not lap["is_complete"]:
                flags.append("incomplete")
            if not lap["is_valid"]:
                flags.append("invalid")
            print(f"  lap_id={lap['lap_id']:<4} lap {lap['lap_number']:<3} "
                  f"{format_laptime(lap['lap_time_ms'])}  "
                  f"{lap['point_count']:5d} pts  {' '.join(flags)}")
        best = db.best_lap(s["session_id"])
        if best:
            print(f"  best: lap {best['lap_number']} "
                  f"({format_laptime(best['lap_time_ms'])}, lap_id={best['lap_id']})")


def show_lap(db: TelemetryDB, lap_id: int, csv_path: str = None) -> int:
    lap = db.get_lap(lap_id)
    if lap is None:
        print(f"lap_id {lap_id} 不存在")
        return 1
    points = db.get_lap_points(lap_id)
    print(f"lap {lap['lap_number']} (session #{lap['session_id']}): "
          f"{format_laptime(lap['lap_time_ms'])}, {len(points)} points, "
          f"valid={bool(lap['is_valid'])}, complete={bool(lap['is_complete'])}")
    if points:
        speeds = [p["speed_kmh"] for p in points]
        print(f"speed: min {min(speeds):.1f} / max {max(speeds):.1f} km/h, "
              f"spline {points[0]['spline']:.3f} -> {points[-1]['spline']:.3f}")
        header = ["t_ms", "spline", "speed_kmh", "throttle", "brake", "steering", "gear", "rpm"]
        if csv_path:
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows([p[h] for h in header] for p in points)
            print(f"已匯出 {csv_path}")
        else:
            print("\n" + "  ".join(f"{h:>9}" for h in header))
            shown = points[:5] + [None] + points[-5:] if len(points) > 10 else points
            for p in shown:
                if p is None:
                    print(f"{'...':>9}")
                    continue
                print("  ".join(f"{p[h]:9.3f}" if isinstance(p[h], float) else f"{p[h]:9d}"
                                for h in header))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="查詢已錄製的遙測資料")
    parser.add_argument("--db", default="data/telemetry.sqlite3")
    parser.add_argument("--lap-id", type=int)
    parser.add_argument("--csv", help="搭配 --lap-id，匯出整圈 CSV")
    args = parser.parse_args()

    db = TelemetryDB(args.db)
    try:
        if args.lap_id is not None:
            return show_lap(db, args.lap_id, args.csv)
        show_overview(db)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
