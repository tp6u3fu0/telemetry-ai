"""555 訓練狀態機測試：四階段流程、失誤重數、無效圈規則、計分。

執行：uv run python tests/test_training.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from training.five55 import Five55, Stage           # noqa: E402


def feed(d, laps):
    """laps: [(time_ms, valid), ...]，回傳每圈 outcome。"""
    return [d.push_lap(t, v) for t, v in laps]


def main() -> int:
    failures = []

    # -- 基準期：失誤打斷連續 --
    d = Five55()
    feed(d, [(90000, True), (90100, True), (90050, True)])
    if len(d.baseline_streak) != 3:
        failures.append(f"基準連續計數錯: {len(d.baseline_streak)}")
    d.push_lap(89000, False)                 # 無效圈 → 歸零
    if d.baseline_streak or d.stage != Stage.BASELINE:
        failures.append("無效圈應打斷基準連續")
    d.push_lap(None, False)                   # 不完整圈也算失誤
    if d.baseline_streak:
        failures.append("不完整圈應打斷基準連續")

    # 5 圈乾淨 → 進「設定超越目標」，A1 = 平均
    out = feed(d, [(90000, True), (90000, True), (90000, True),
                   (90000, True), (90200, True)])
    if out[-1] != "streak_done" or d.stage != Stage.SET_BEAT:
        failures.append(f"基準 5 圈應進設定超越目標: {d.stage}")
    if d.baseline_avg != 90040:
        failures.append(f"A1 計算錯: {d.baseline_avg}")

    # -- 設定超越目標（round 2 自訂進步時間） --
    if d.suggested_beat_target() != 89740:        # A1 快 0.3 秒
        failures.append(f"建議超越目標錯: {d.suggested_beat_target()}")
    if d.push_lap(89000, True) != "waiting":
        failures.append("設定超越目標前的圈應為 waiting")
    if not d.set_beat_target(89740):
        failures.append("set_beat_target 應成功")
    if d.stage != Stage.BEAT:
        failures.append("設定超越目標後應進超越期")

    # -- 超越期：超過超越目標 或 失誤都重數 --
    d.push_lap(89000, True)                   # ≤ 目標 → 累積 1
    d.push_lap(89500, True)                   # ≤ 目標 → 累積 2
    if len(d.beat_streak) != 2:
        failures.append(f"超越連續錯: {len(d.beat_streak)}")
    d.push_lap(90500, True)                   # 超過超越目標 → 歸零
    if d.beat_streak:
        failures.append("超過超越目標應打斷超越連續")
    d.push_lap(89000, True)
    d.push_lap(89000, False)                  # 有效速度但無效圈 → 歸零
    if d.beat_streak:
        failures.append("無效圈應打斷超越連續（即使很快）")
    # 5 圈都 ≤ 目標且乾淨 → 進設定達標目標，A2
    out = feed(d, [(89000, True), (89000, True), (89000, True),
                   (89000, True), (89000, True)])
    if out[-1] != "streak_done" or d.stage != Stage.SET_TARGET:
        failures.append(f"超越 5 圈應進設定達標目標: {d.stage}")
    if d.beat_avg != 89000:
        failures.append(f"A2 計算錯: {d.beat_avg}")

    # -- 設定達標目標 --
    if d.suggested_target() != 88500:
        failures.append(f"建議目標錯: {d.suggested_target()}")
    if d.push_lap(88000, True) != "waiting":
        failures.append("設定目標前的圈應為 waiting")
    if not d.set_target(88500):
        failures.append("set_target 應成功")
    if d.stage != Stage.ACHIEVE:
        failures.append("設定目標後應進達標期")

    # -- 達標期：累積 5 圈 ≤ T，無效圈不算數也不重置 --
    d.push_lap(88400, True)                   # 達標 1
    d.push_lap(89000, True)                   # miss（有效但慢）
    d.push_lap(88000, False)                  # 無效 → ignored，不算不影響
    if len(d.achieve_qualified) != 1 or d.achieve_attempts != 2:
        failures.append(f"達標計數錯: qual={len(d.achieve_qualified)} "
                        f"attempts={d.achieve_attempts}")
    out = feed(d, [(88400, True), (88300, True), (88200, True), (88100, True)])
    if d.stage != Stage.DONE:
        failures.append(f"達標 5 圈應完成: {d.stage}")
    if out[-1] != "qualify_done":
        failures.append("最後一圈 outcome 應為 qualify_done")

    # -- 計分 --
    sc = d.score
    if sc is None or not (0 <= sc["total"] <= 100):
        failures.append(f"總分異常: {sc}")
    else:
        # attempts = 6（88400,89000,88400,88300,88200,88100；無效圈不計）
        if sc["attempts"] != 6:
            failures.append(f"attempts 錯: {sc['attempts']}")
        # 效率 = 5/6 ≈ 83
        if abs(sc["efficiency"] - 83) > 2:
            failures.append(f"效率分錯: {sc['efficiency']}")
        for k in ("consistency", "improvement", "ambition", "efficiency"):
            if not (0 <= sc[k] <= 100):
                failures.append(f"{k} 超出範圍: {sc[k]}")

    # -- state() 序列化 --
    st = d.state()
    if st["stage"] != "done" or st["score"]["total"] != sc["total"]:
        failures.append("state() 不一致")

    # -- to_dict / from_dict：暫停續傳的完整還原 --
    mid = Five55()
    feed(mid, [(90000, True), (90100, True), (90050, True)])  # 基準期跑到 3/5
    restored = Five55.from_dict(mid.to_dict())
    if (restored.stage != Stage.BASELINE
            or restored.baseline_streak != mid.baseline_streak
            or len(restored.history) != 3):
        failures.append(f"續傳還原錯: {restored.to_dict()}")
    # 續傳後接著跑 2 圈應完成基準期（連續不因暫停中斷）→ 進設定超越目標
    feed(restored, [(90000, True), (90000, True)])
    if restored.stage != Stage.SET_BEAT:
        failures.append("續傳後應能接續完成連續")
    # 完成態 round-trip 保留分數
    if Five55.from_dict(d.to_dict()).score != d.score:
        failures.append("完成態續傳分數遺失")

    # -- 完美一致性 → 高一致性分 --
    d2 = Five55()
    feed(d2, [(90000, True)] * 5)             # 全同 → spread 0
    if d2.baseline_avg != 90000:
        failures.append("完美基準 A1 錯")

    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        return 1
    print(f"PASS  (四階段/失誤重數/無效圈規則/計分正確，示範總分 {sc['total']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
