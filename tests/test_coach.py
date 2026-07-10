"""階段四測試：彎道對照、教練 context 組裝；有 API 金鑰時額外做一次 live 呼叫。

執行：uv run python tests/test_coach.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import coach                                    # noqa: E402
from analysis.corners import (                             # noqa: E402
    annotate_zones, corner_at, load_corners)


class FakeZone:
    def __init__(self, index, start, apex):
        self.index, self.start, self.apex = index, start, apex


def main() -> int:
    failures = []

    # -- 彎道對照表 --
    corners = load_corners("acc", "monza")
    if not corners or len(corners) < 6:
        failures.append(f"monza 對照表載入失敗: {corners}")
    else:
        cases = [(0.14, "Variante del Rettifilo"), (0.35, "Variante della Roggia"),
                 (0.42, "Lesmo 1"), (0.48, "Lesmo 2"),
                 (0.66, "Variante Ascari"), (0.87, "Parabolica (Alboreto)")]
        for spline, expected in cases:
            c = corner_at(corners, spline)
            if c is None or c["name"] != expected:
                failures.append(f"spline {spline} 應為 {expected}，得 {c}")
        if corner_at(corners, 0.99) is not None:   # 主直線，不在任何彎附近
            failures.append("主直線 0.99 不應對到彎")

    # 未知賽道/遊戲 → 優雅退化
    if load_corners("acc", "no_such_track") is not None:
        failures.append("未知賽道應回傳 None")
    if load_corners("f1_25", "monza") is not None:
        failures.append("未建表的遊戲應回傳 None")

    # 以使用者實際遙測的區段位置驗證 annotate（#5: 65.2%, apex ~67%）
    zones = [FakeZone(1, 0.128, 0.15), FakeZone(5, 0.652, 0.67),
             FakeZone(9, 0.985, 0.99)]
    names = annotate_zones(zones, corners)
    if "Rettifilo" not in names.get(1, ""):
        failures.append(f"zone1 應對到 Rettifilo: {names.get(1)}")
    if "Ascari" not in names.get(5, ""):
        failures.append(f"zone5 應對到 Ascari: {names.get(5)}")
    if 9 in names:
        failures.append("直線上的區段不應有彎名")

    # -- 教練 context 組裝 --
    blocks = coach.build_context("摘要內容：B 共慢 +1.357 秒", "monza", "ferrari_296_gt3")
    text_all = " ".join(b["text"] for b in blocks)
    if "賽車教練" not in blocks[0]["text"]:
        failures.append("persona 遺失")
    if "速度聖殿" not in text_all:
        failures.append("Monza 知識文件未載入")
    if "B 共慢 +1.357" not in text_all:
        failures.append("遙測摘要未進 context")
    if "cache_control" not in blocks[-1]:
        failures.append("最後一個 block 應掛 cache_control")
    # 未知賽道：知識文件缺席但不噴錯
    blocks2 = coach.build_context("x", "unknown_track", "car")
    if any("速度聖殿" in b["text"] for b in blocks2):
        failures.append("未知賽道不應載入 monza 知識")

    # -- live smoke（僅在有金鑰時） --
    if coach.has_credentials() and os.environ.get("COACH_LIVE_TEST") == "1":
        try:
            reply = coach.ask(blocks, [{"role": "user", "content": "一句話：我最該改進哪裡？"}])
            if not reply or len(reply) < 5:
                failures.append(f"live 回覆異常: {reply!r}")
            else:
                print(f"[live] 教練回覆: {reply[:120]}...")
        except Exception as exc:
            failures.append(f"live 呼叫失敗: {exc!r}")
    else:
        print("[skip] 無 API 金鑰或未設 COACH_LIVE_TEST=1，跳過 live 測試")

    if failures:
        print("FAIL")
        for f in failures:
            print(" -", f)
        return 1
    print("PASS  (彎道對照 + context 組裝正確)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
