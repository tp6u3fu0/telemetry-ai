"""555 訓練狀態機（純邏輯，可離線測試）。

訓練法概念源自賽車一丁的影片《不養成好習慣, 就是在培養一個壞習慣》
（https://youtu.be/_JJEQIiwphw）。

階段：
  1. 基準期    連續 5 圈 0 失誤 → 平均 A1（失誤=無效/不完整圈，會打斷連續歸零重數）
  2. 設定超越目標 玩家依 A1 自訂超越目標時間 B（預設 A1 快 0.3 秒）
  3. 超越期    連續 5 圈每圈都乾淨且 ≤ B → 平均 A2（超過 B 或失誤都歸零重數）
  4. 設定目標  玩家依 A2 自訂達標目標時間 T（預設 A2 快 0.5 秒）
  5. 達標期    在剩餘圈中累積 5 圈 ≤ T（不需連續；無效圈不算數也不影響）→ 完成計分

失誤定義：無效圈（出界/切彎）或不完整圈。這類圈一律不計圈速——
連續階段會打斷連續，達標階段單純略過。

得分：一致性/進步幅度/目標企圖心/達標效率四項各 0–100，加權為總分。
門檻常數以 GT3/F1 的圈速尺度設定，可調（見各 _score_* 註解）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

STREAK_LEN = 5
ACHIEVE_TARGET = 5


class Stage(str, Enum):
    BASELINE = "baseline"       # 基準期
    SET_BEAT = "set_beat"       # 等待設定超越目標
    BEAT = "beat"               # 超越期
    SET_TARGET = "set_target"   # 等待設定達標目標
    ACHIEVE = "achieve"         # 達標期
    DONE = "done"               # 完成


@dataclass
class LapRecord:
    n: int
    time_ms: int | None
    good: bool                  # 乾淨且完整
    stage: str
    outcome: str                # streak / reset / streak_done / qualify /
                                # miss / ignored / waiting / qualify_done


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


@dataclass
class Five55:
    stage: Stage = Stage.BASELINE
    baseline_streak: list = field(default_factory=list)
    baseline_avg: int | None = None
    beat_target_ms: int | None = None   # round 2 自訂超越目標
    beat_streak: list = field(default_factory=list)
    beat_avg: int | None = None
    target_ms: int | None = None
    achieve_qualified: list = field(default_factory=list)
    achieve_attempts: int = 0    # 達標期中的「有效圈」數（無效圈不計）
    history: list = field(default_factory=list)
    score: dict | None = None

    # -- 推進 ---------------------------------------------------------------

    def push_lap(self, time_ms, valid: bool) -> str:
        """餵入一個完成的圈。valid 已含「乾淨且完整」語意；time_ms 無值=不完整。"""
        good = bool(valid) and time_ms is not None
        outcome = self._advance(good, time_ms)
        self.history.append(LapRecord(len(self.history) + 1, time_ms,
                                      good, self.stage.value, outcome))
        return outcome

    def _advance(self, good: bool, time_ms) -> str:
        if self.stage == Stage.BASELINE:
            if not good:
                self.baseline_streak = []
                return "reset"
            self.baseline_streak.append(time_ms)
            if len(self.baseline_streak) >= STREAK_LEN:
                self.baseline_avg = round(sum(self.baseline_streak) / STREAK_LEN)
                self.stage = Stage.SET_BEAT
                return "streak_done"
            return "streak"

        if self.stage == Stage.SET_BEAT:
            return "waiting"      # 超越目標未定，圈暫不評估（照常存進 DB）

        if self.stage == Stage.BEAT:
            if not good or time_ms > self.beat_target_ms:
                self.beat_streak = []
                return "reset"
            self.beat_streak.append(time_ms)
            if len(self.beat_streak) >= STREAK_LEN:
                self.beat_avg = round(sum(self.beat_streak) / STREAK_LEN)
                self.stage = Stage.SET_TARGET
                return "streak_done"
            return "streak"

        if self.stage == Stage.SET_TARGET:
            return "waiting"      # 目標未定，圈暫不評估（照常存進 DB）

        if self.stage == Stage.ACHIEVE:
            if not good:
                return "ignored"  # 無效圈不算數、不影響已累積
            self.achieve_attempts += 1
            if time_ms <= self.target_ms:
                self.achieve_qualified.append(time_ms)
                if len(self.achieve_qualified) >= ACHIEVE_TARGET:
                    self.stage = Stage.DONE
                    self._compute_score()
                    return "qualify_done"
                return "qualify"
            return "miss"

        return "done"

    def apply_target(self, ms) -> bool:
        """依目前階段設定對應的目標（超越目標 or 達標目標），並推進。"""
        if self.stage == Stage.SET_BEAT:
            return self.set_beat_target(ms)
        if self.stage == Stage.SET_TARGET:
            return self.set_target(ms)
        return False

    def set_beat_target(self, target_ms) -> bool:
        if self.stage != Stage.SET_BEAT:
            return False
        self.beat_target_ms = int(target_ms)
        self.stage = Stage.BEAT
        return True

    def set_target(self, target_ms) -> bool:
        if self.stage != Stage.SET_TARGET:
            return False
        self.target_ms = int(target_ms)
        self.stage = Stage.ACHIEVE
        return True

    def suggested_beat_target(self) -> int | None:
        """預設建議：A1 快 0.3 秒。"""
        return None if self.baseline_avg is None else max(1, self.baseline_avg - 300)

    def suggested_target(self) -> int | None:
        """預設建議：A2 快 0.5 秒。"""
        return None if self.beat_avg is None else max(1, self.beat_avg - 500)

    # -- 計分 ---------------------------------------------------------------

    def _compute_score(self) -> None:
        b = self.baseline_streak
        mean = sum(b) / len(b)
        spread = (max(b) - min(b)) / mean            # 5 圈相對全距
        consistency = _clamp(100 * (1 - spread / 0.01))          # 1% 全距 → 0 分
        imp = (self.baseline_avg - self.beat_avg) / self.baseline_avg
        improvement = _clamp(imp / 0.015 * 100)                  # 快 1.5% → 100
        amb = (self.beat_avg - self.target_ms) / self.beat_avg
        ambition = _clamp(amb / 0.015 * 100)                     # 目標低 1.5% → 100
        efficiency = _clamp(ACHIEVE_TARGET / self.achieve_attempts * 100)
        total = round(consistency * 0.25 + improvement * 0.25
                      + ambition * 0.20 + efficiency * 0.30)
        self.score = {
            "consistency": round(consistency),
            "improvement": round(improvement),
            "ambition": round(ambition),
            "efficiency": round(efficiency),
            "total": total,
            "attempts": self.achieve_attempts,
        }

    # -- 序列化（給 API / UI） ----------------------------------------------

    _STAGE_LABEL = {
        Stage.BASELINE: "基準期", Stage.SET_BEAT: "設定超越目標",
        Stage.BEAT: "超越期", Stage.SET_TARGET: "設定達標目標",
        Stage.ACHIEVE: "達標期", Stage.DONE: "完成",
    }

    def requirement(self) -> str:
        if self.stage == Stage.BASELINE:
            return f"連續 5 圈零失誤（目前 {len(self.baseline_streak)}/5）"
        if self.stage == Stage.SET_BEAT:
            return f"設定你的超越目標圈速（基準均速 {_fmt(self.baseline_avg)}）"
        if self.stage == Stage.BEAT:
            return (f"連續 5 圈 ≤ 超越目標 {_fmt(self.beat_target_ms)}"
                    f"（目前 {len(self.beat_streak)}/5）")
        if self.stage == Stage.SET_TARGET:
            return f"設定你的達標目標圈速（超越期均速 {_fmt(self.beat_avg)}）"
        if self.stage == Stage.ACHIEVE:
            return (f"累積 5 圈 ≤ {_fmt(self.target_ms)}"
                    f"（目前 {len(self.achieve_qualified)}/5）")
        return "訓練完成"

    def state(self) -> dict:
        return {
            "stage": self.stage.value,
            "stage_label": self._STAGE_LABEL[self.stage],
            "requirement": self.requirement(),
            "baseline_progress": len(self.baseline_streak),
            "baseline_avg": self.baseline_avg,
            "beat_target_ms": self.beat_target_ms,
            "suggested_beat_target": self.suggested_beat_target(),
            "beat_progress": len(self.beat_streak),
            "beat_avg": self.beat_avg,
            "target_ms": self.target_ms,
            "suggested_target": self.suggested_target(),
            "achieve_progress": len(self.achieve_qualified),
            "achieve_attempts": self.achieve_attempts,
            "score": self.score,
            "recent": [
                {"n": r.n, "time_ms": r.time_ms, "good": r.good,
                 "stage": r.stage, "outcome": r.outcome}
                for r in self.history[-8:]
            ],
        }

    # -- 存檔 / 續傳（完整狀態，非 state() 的精簡版） ------------------------

    def to_dict(self) -> dict:
        """完整快照——含 streak/history/階段，可原樣還原以續傳暫停的訓練。"""
        return {
            "stage": self.stage.value,
            "baseline_streak": list(self.baseline_streak),
            "baseline_avg": self.baseline_avg,
            "beat_target_ms": self.beat_target_ms,
            "beat_streak": list(self.beat_streak),
            "beat_avg": self.beat_avg,
            "target_ms": self.target_ms,
            "achieve_qualified": list(self.achieve_qualified),
            "achieve_attempts": self.achieve_attempts,
            "history": [
                {"n": r.n, "time_ms": r.time_ms, "good": r.good,
                 "stage": r.stage, "outcome": r.outcome}
                for r in self.history
            ],
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Five55":
        f = cls(
            stage=Stage(d.get("stage", Stage.BASELINE.value)),
            baseline_streak=list(d.get("baseline_streak", [])),
            baseline_avg=d.get("baseline_avg"),
            beat_target_ms=d.get("beat_target_ms"),
            beat_streak=list(d.get("beat_streak", [])),
            beat_avg=d.get("beat_avg"),
            target_ms=d.get("target_ms"),
            achieve_qualified=list(d.get("achieve_qualified", [])),
            achieve_attempts=int(d.get("achieve_attempts", 0)),
            score=d.get("score"),
        )
        f.history = [
            LapRecord(r["n"], r["time_ms"], r["good"], r["stage"], r["outcome"])
            for r in d.get("history", [])
        ]
        return f


def _fmt(ms) -> str:
    if not ms or ms <= 0:
        return "--:--.---"
    m, rem = divmod(int(ms), 60000)
    s, milli = divmod(rem, 1000)
    return f"{m}:{s:02d}.{milli:03d}"
