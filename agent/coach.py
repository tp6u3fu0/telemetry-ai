"""AI 賽車教練：把遙測分析結果餵給 Claude，以教練口吻給出具體建議。

架構：
- build_context()：組出 system prompt——教練人設 + 賽道知識文件 + 本次兩圈的分析摘要。
  這些是「每次對話都相同」的穩定前綴，掛 cache_control 讓多輪追問吃 prompt cache。
- ask()：無狀態；對話歷史由前端持有、每次整包送來（Messages API 本身無狀態）。
"""
from __future__ import annotations

import os
from pathlib import Path

import anthropic

from webapp.config import load_config

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"


def get_api_key() -> str | None:
    """金鑰來源優先序：app 設定檔 > 環境變數。"""
    key = load_config().get("anthropic_api_key", "").strip()
    return key or os.environ.get("ANTHROPIC_API_KEY") or None


def get_model() -> str:
    """模型優先序：COACH_MODEL 環境變數 > app 設定檔 > 預設 sonnet。"""
    return (os.environ.get("COACH_MODEL")
            or load_config().get("coach_model")
            or "claude-sonnet-5")

_COACH_PERSONA = """\
你是一位資深 GT3 賽車教練，正在幫車手分析 Assetto Corsa Competizione 的遙測資料。

你的風格：
- 具體、可執行：講「煞車點、煞車力道釋放、油門介入時機、走線」，不講空泛話術
- 依時間價值排優先順序：先講損失最大的彎，一次只給 1-3 個最重要的改進點
- 用數據佐證：引用遙測摘要中的實際數字（煞車點差幾 %、彎中最低速差幾 km/h、損失幾秒）
- 區分「進彎損失」與「出彎損失」：進彎損失通常是煞車點/煞車技巧問題，
  出彎損失通常是 apex 速度過高導致出彎收油、或油門介入太晚——處方完全不同
- 注意：比較圈在某彎「賺」的時間如果伴隨出口速度損失，往往是犧牲出彎的晚煞——
  在接長直線的彎這是賠錢生意，要指出來
- 使用繁體中文，稱呼對方「你」，語氣像 pit wall 上的無線電：直接、簡潔、專業

資料說明：
- 「A」是參考圈（通常是最快圈），「B」是被比較的圈。損失為正 = B 較慢
- 賽道位置以 spline %（賽道總長百分比）表示
- 車手的追問可能指向特定彎道，用賽道知識中的彎名對應遙測摘要中的區段位置\
"""


def _load_knowledge(track: str) -> str | None:
    if not track:
        return None
    path = _KNOWLEDGE_DIR / f"{track.strip().lower()}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def build_context(summary: str, track: str, car: str) -> list:
    """組出 system blocks（穩定內容在前 + cache_control，供多輪對話重用）。"""
    blocks = [{"type": "text", "text": _COACH_PERSONA}]
    knowledge = _load_knowledge(track)
    if knowledge:
        blocks.append({"type": "text",
                       "text": f"## 賽道知識\n\n{knowledge}"})
    blocks.append({
        "type": "text",
        "text": (f"## 本次分析\n賽道：{track or '未知'}\n車輛：{car or '未知'}\n\n"
                 f"### 遙測比較摘要\n{summary}"),
        "cache_control": {"type": "ephemeral"},
    })
    return blocks


def has_credentials() -> bool:
    return bool(get_api_key() or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _client() -> anthropic.Anthropic:
    key = load_config().get("anthropic_api_key", "").strip()
    # 設定檔有金鑰就明確使用它；否則交給 SDK 的環境變數解析
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def verify_key() -> tuple[bool, str]:
    """用 count_tokens（免費端點）驗證金鑰與模型可用性。"""
    try:
        _client().messages.count_tokens(
            model=get_model(),
            messages=[{"role": "user", "content": "ping"}],
        )
        return True, f"連線成功（模型：{get_model()}）"
    except anthropic.AuthenticationError:
        return False, "金鑰無效，請確認是否完整複製（sk-ant- 開頭）"
    except anthropic.NotFoundError:
        return False, f"模型 {get_model()} 不存在或無權限"
    except anthropic.APIConnectionError:
        return False, "無法連線到 Claude API，請檢查網路"
    except anthropic.APIStatusError as exc:
        return False, f"API 錯誤（{exc.status_code}）"


def ask(system_blocks: list, messages: list) -> str:
    """messages: [{"role": "user"|"assistant", "content": str}, ...]（完整歷史）"""
    response = _client().messages.create(
        model=get_model(),
        max_tokens=2048,
        system=system_blocks,
        messages=messages,
    )
    return "".join(b.text for b in response.content if b.type == "text")
