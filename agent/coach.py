"""AI 賽車教練：把遙測分析結果餵給 LLM，以教練口吻給出具體建議。

支援多供應商（設定檔 coach_provider 切換）：
- anthropic：Claude（原生 SDK，支援 prompt cache）
- openai：OpenAI 官方
- google：Gemini（經 OpenAI 相容端點）
- local：任何 OpenAI 相容伺服器（Ollama / LM Studio / llama.cpp …）

openai / google / local 三者共用 openai SDK，只差 base_url 與金鑰。

build_context() 組出「教練人設 + 賽道知識 + 本次兩圈分析摘要」的中性結構，
再由各供應商 adapter 轉成自己的格式（Anthropic 用 system blocks + cache_control，
OpenAI 相容用單一 system message）。對話無狀態，歷史由前端整包送來。
"""
from __future__ import annotations

import os
from pathlib import Path

from webapp.config import load_config

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

# 各供應商的預設模型（使用者可在設定覆寫）
_DEFAULT_MODEL = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o",
    "google": "gemini-2.0-flash",
    "local": "llama3.1",
}
_GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"

_COACH_PERSONA = """\
你是一位資深 GT3 賽車教練，正在幫車手分析模擬賽車的遙測資料。

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


# ---- 供應商 / 模型 / 金鑰 --------------------------------------------------

def get_provider() -> str:
    return (os.environ.get("COACH_PROVIDER")
            or load_config().get("coach_provider") or "anthropic")


def get_model() -> str:
    """模型優先序：COACH_MODEL 環境變數 > app 設定檔 > 供應商預設。"""
    return (os.environ.get("COACH_MODEL")
            or load_config().get("coach_model")
            or _DEFAULT_MODEL.get(get_provider(), "claude-sonnet-5"))


def _api_key(provider: str) -> str:
    cfg = load_config()
    if provider == "anthropic":
        return (cfg.get("anthropic_api_key", "").strip()
                or os.environ.get("ANTHROPIC_API_KEY") or "")
    if provider == "openai":
        return (cfg.get("openai_api_key", "").strip()
                or os.environ.get("OPENAI_API_KEY") or "")
    if provider == "google":
        return (cfg.get("google_api_key", "").strip()
                or os.environ.get("GOOGLE_API_KEY")
                or os.environ.get("GEMINI_API_KEY") or "")
    if provider == "local":
        return cfg.get("local_api_key", "").strip() or "not-needed"
    return ""


def has_credentials() -> bool:
    """local 只要有 base_url 就算；其餘需有金鑰。"""
    provider = get_provider()
    if provider == "local":
        return bool(load_config().get("local_base_url", "").strip())
    return bool(_api_key(provider))


# ---- context 組裝（中性結構 → 各供應商格式） ------------------------------

def _load_knowledge(track: str) -> str | None:
    if not track:
        return None
    path = _KNOWLEDGE_DIR / f"{track.strip().lower()}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def build_context(summary: str, track: str, car: str) -> dict:
    """中性 context：由 ask_stream 依供應商轉成對應格式。"""
    analysis = (f"## 本次分析\n賽道：{track or '未知'}\n車輛：{car or '未知'}\n\n"
                f"### 遙測比較摘要\n{summary}")
    return {"persona": _COACH_PERSONA,
            "knowledge": _load_knowledge(track),
            "analysis": analysis}


def _anthropic_system(context: dict) -> list:
    """system blocks：穩定內容在前並掛 cache_control，供多輪對話重用。"""
    blocks = [{"type": "text", "text": context["persona"]}]
    if context.get("knowledge"):
        blocks.append({"type": "text",
                       "text": f"## 賽道知識\n\n{context['knowledge']}"})
    blocks.append({"type": "text", "text": context["analysis"],
                   "cache_control": {"type": "ephemeral"}})
    return blocks


def _system_text(context: dict) -> str:
    """單一 system 字串（OpenAI 相容供應商用）。"""
    parts = [context["persona"]]
    if context.get("knowledge"):
        parts.append(f"## 賽道知識\n\n{context['knowledge']}")
    parts.append(context["analysis"])
    return "\n\n".join(parts)


# ---- 客戶端 ----------------------------------------------------------------

def _anthropic_client():
    import anthropic
    key = _api_key("anthropic")
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def _openai_client(provider: str):
    """openai / google / local 共用——只差 base_url 與金鑰。"""
    from openai import OpenAI
    key = _api_key(provider)
    if provider == "google":
        return OpenAI(api_key=key, base_url=_GOOGLE_BASE)
    if provider == "local":
        base = load_config().get("local_base_url", "").strip() \
            or "http://localhost:11434/v1"
        return OpenAI(api_key=key, base_url=base)
    return OpenAI(api_key=key)          # openai 官方


# ---- 驗證 ------------------------------------------------------------------

def verify_key() -> tuple[bool, str]:
    provider = get_provider()
    model = get_model()
    try:
        if provider == "anthropic":
            _anthropic_client().messages.count_tokens(
                model=model, messages=[{"role": "user", "content": "ping"}])
        else:
            _openai_client(provider).models.list()   # 驗證金鑰/連線
        return True, f"連線成功（{provider} · {model}）"
    except Exception as exc:                          # noqa: BLE001
        return False, f"連線失敗：{_short_err(exc)}"


def _short_err(exc: Exception) -> str:
    msg = str(exc) or exc.__class__.__name__
    return msg if len(msg) <= 160 else msg[:157] + "…"


# ---- 串流 ------------------------------------------------------------------

def ask_stream(context: dict, messages: list):
    """逐段 yield 文字。messages: [{"role","content"}, ...]（完整歷史）。"""
    provider = get_provider()
    if provider == "anthropic":
        yield from _anthropic_stream(context, messages)
    else:
        yield from _openai_stream(provider, context, messages)


def _anthropic_stream(context: dict, messages: list):
    with _anthropic_client().messages.stream(
        model=get_model(), max_tokens=2048,
        system=_anthropic_system(context), messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text


def _openai_stream(provider: str, context: dict, messages: list):
    client = _openai_client(provider)
    msgs = [{"role": "system", "content": _system_text(context)}] + messages
    # 不設 max_tokens——各家新舊模型參數名不一（max_tokens vs max_completion_tokens），
    # 交給伺服器預設最省事
    stream = client.chat.completions.create(
        model=get_model(), messages=msgs, stream=True)
    for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
