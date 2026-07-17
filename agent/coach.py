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
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(name: str) -> str:
    """讀取 agent/prompts/{name}.md（prompt 檔案化，改 prompt 不用動程式）。"""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")

# 各供應商的預設模型（使用者可在設定覆寫）
_DEFAULT_MODEL = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o",
    "google": "gemini-2.0-flash",
    "local": "llama3.1",
}
_GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"

# persona：角色 + 分析方法（CoT-lite）+ 回覆格式契約 + 資料慣例
# examples：兩則 few-shot 完整範例（比較圈 / 單圈），示範格式與判讀方式
# report_format：結構化分析報告的 JSON schema 指示（ask_report 用）
_COACH_PERSONA = _load_prompt("persona")
_FEWSHOT_EXAMPLES = _load_prompt("examples")
_REPORT_FORMAT = _load_prompt("report_format")


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
            "examples": _FEWSHOT_EXAMPLES,
            "analysis": analysis}


def _anthropic_system(context: dict) -> list:
    """system blocks：穩定內容在前並掛 cache_control，供多輪對話重用。

    順序 persona → knowledge → examples → analysis；cache_control 掛在最後的
    analysis block，Anthropic 會快取整段前綴，few-shot 範例每組圈只花一次
    input token，後續輪次近乎免費。
    """
    blocks = [{"type": "text", "text": context["persona"]}]
    if context.get("knowledge"):
        blocks.append({"type": "text",
                       "text": f"## 賽道知識\n\n{context['knowledge']}"})
    if context.get("examples"):
        blocks.append({"type": "text", "text": context["examples"]})
    blocks.append({"type": "text", "text": context["analysis"],
                   "cache_control": {"type": "ephemeral"}})
    return blocks


def _system_text(context: dict) -> str:
    """單一 system 字串（OpenAI 相容供應商用）。"""
    parts = [context["persona"]]
    if context.get("knowledge"):
        parts.append(f"## 賽道知識\n\n{context['knowledge']}")
    if context.get("examples"):
        parts.append(context["examples"])
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
        model=get_model(), max_tokens=3000,   # 格式契約讓回答略長，避免截斷
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


# ---- 結構化分析報告 --------------------------------------------------------

def ask_report(context: dict) -> tuple[dict | None, str]:
    """一次性產出結構化報告。回傳 (parsed, raw)：parsed 解析失敗時為 None。

    刻意用 prompt-based JSON + 容錯解析，而非各供應商的 tool-use /
    response_format——四路（Anthropic/OpenAI/Gemini/local）行為才一致。
    system 前綴與聊天完全相同，同組圈的 Anthropic prompt cache 可共用。
    """
    messages = [{"role": "user", "content": _REPORT_FORMAT}]
    provider = get_provider()
    if provider == "anthropic":
        resp = _anthropic_client().messages.create(
            model=get_model(), max_tokens=3000,
            system=_anthropic_system(context), messages=messages)
        raw = "".join(b.text for b in resp.content if b.type == "text")
    else:
        client = _openai_client(provider)
        resp = client.chat.completions.create(
            model=get_model(),
            messages=[{"role": "system", "content": _system_text(context)}]
                     + messages)
        raw = resp.choices[0].message.content or ""
    return parse_report(raw), raw


def parse_report(raw: str) -> dict | None:
    """容錯解析模型輸出的 JSON：剝 code fence、取最外層大括號、驗證形狀。"""
    import json
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]        # 去掉 ```json 首行
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return None
    data.setdefault("overall", "")
    return data
