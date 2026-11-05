"""LLM client — chat, classification and the ticket assistant.

Backends are switched by env (see config.py): mock | openai | ollama.
Everything goes through the standard library, so the merged app needs no new
runtime dependency for the model path.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from . import config

TIMEOUT = 90


def _post_json(url: str, headers: dict, payload: dict, timeout: int = TIMEOUT) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------- chat


def chat(messages: list[dict]) -> dict:
    """messages: [{"role": "user"/"assistant", "content": "..."}] → {"reply", "backend"}"""
    backend = config.CHAT_BACKEND
    if backend == "openai":
        return {"reply": _chat_openai_text(messages), "backend": "openai"}
    if backend == "ollama":
        return {"reply": _chat_ollama(messages), "backend": "ollama"}
    return {"reply": _chat_mock(messages), "backend": "mock"}


def _chat_mock(messages: list[dict]) -> str:
    last = [m for m in messages if m.get("role") == "user"]
    text = last[-1]["content"] if last else ""
    return (
        "【模拟模式】已收到你的消息：\n"
        f"「{text}」\n\n"
        "当前未接真实大模型。设置 CHAT_BACKEND=openai（并填 OPENAI_API_KEY）或 =ollama 后，"
        "这里就会返回真实大模型的回答。监测看板不受影响，它始终能用规则给出结论。"
    )


def _chat_openai_message(messages: list[dict], tools: list[dict] | None = None) -> dict:
    payload = {"model": config.OPENAI_MODEL, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    data = _post_json(
        f"{config.OPENAI_BASE_URL}/chat/completions",
        {"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        payload,
    )
    return data["choices"][0]["message"]


def _chat_openai_text(messages: list[dict]) -> str:
    return _chat_openai_message(messages).get("content") or ""


def _chat_ollama(messages: list[dict]) -> str:
    data = _post_json(
        f"{config.OLLAMA_BASE_URL}/api/chat",
        {},
        {"model": config.OLLAMA_MODEL, "messages": messages, "stream": False},
    )
    return data["message"]["content"]


# ---------------------------------------------------------------- ticket assistant

ASSISTANT_SYSTEM_PROMPT = """你是 CityU 校园设施报修智能助手，服务于设施管理团队（YMK 4/F 中庭试点）。

你的工作流程：
1. 用户描述设施问题时，先确认信息是否齐全：具体位置(zone)、问题描述、紧急程度。缺信息时用一句话追问，不要反复盘问。
2. 信息齐全后，**先调用 find_duplicates 检查是否有重复工单**（同位置+同类问题）；若有相似工单，向用户说明并建议合并，不要重复创建。
3. 确认无重复后，调用 create_ticket 创建工单，并把生成的工单编号告知用户。
4. 用户问工单进度时，调用 track_ticket 查询状态。

规则：
- 始终用中文回复，简洁、口语化，不要输出 JSON 或代码。
- 用户没有明确位置时，默认引导使用 YKM-4-CONC 开头的位置代码；不知道区域就调用 get_zones 查看可用区域。
- 紧急程度 impact 取值：BLOCKED/SAFETY/EMERGENCY → P1；MINOR → P2；INCONVENIENCE → P3。拿不准就用 P3。
- 只做与设施报修相关的事，其他话题礼貌拒绝。
"""


def assistant(messages: list[dict], api, image_url: str = "") -> dict:
    """Ticket assistant: system prompt + function-calling loop.

    `api` is the local self-client (see tools.Api) so tool calls hit this same
    service instead of a separate deployment.
    """
    from .tools import TOOL_DEFS, execute

    if config.CHAT_BACKEND != "openai":
        return {
            "reply": "工单助手需要接入 OpenAI 兼容 API（当前后端为 " + config.CHAT_BACKEND + "）。",
            "steps": [], "backend": config.CHAT_BACKEND,
        }

    history = [{"role": "system", "content": ASSISTANT_SYSTEM_PROMPT}]
    if image_url:
        history.append({"role": "user", "content": [
            {"type": "text", "text": "（用户附了一张现场照片，请结合对话上下文处理）"},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]})
    history.extend(messages[-8:])

    steps: list[dict] = []
    for _ in range(6):
        msg = _chat_openai_message(history, tools=TOOL_DEFS)
        if not msg.get("tool_calls"):
            return {"reply": msg.get("content") or "（模型没有返回内容）",
                    "steps": steps, "backend": config.CHAT_BACKEND}
        history.append({"role": "assistant", "content": msg.get("content") or "",
                        "tool_calls": msg["tool_calls"]})
        for tc in msg["tool_calls"]:
            fn = tc["function"]
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            try:
                result, status = execute(name, args, api), "ok"
            except Exception as exc:
                result, status = {"error": f"{type(exc).__name__}: {exc}"}, "error"
            steps.append({"tool": name, "args": args, "result": result, "status": status})
            history.append({"role": "tool", "tool_call_id": tc["id"],
                            "content": json.dumps(result, ensure_ascii=False)})
    return {"reply": "工具调用次数过多，已停止。请重试。", "steps": steps,
            "backend": config.CHAT_BACKEND}


# ---------------------------------------------------------------- classification

GROUPS = ["SIGN", "CLEAN", "SECU", "AUTO", "EVENT", "HVAC", "SAFE", "MOVE",
          "RENO", "LAND", "LOCK", "MAIL", "KEY", "OTHR"]

_FALLBACK_KEYWORDS = {
    "SIGN": ["sign", "wayfind", "poster", "direction", "exit"],
    "CLEAN": ["clean", "dirty", "smell", "spill", "bin", "trash", "stain"],
    "SECU": ["security", "alarm", "suspicious", "cctv", "tailgat"],
    "AUTO": ["door", "sliding", "sensor", "entrance"],
    "HVAC": ["aircon", "air con", "light", "bulb", "temperature", "cold", "hot",
             "leak", "water", "plug", "socket", "toilet", "fan"],
    "SAFE": ["first aid", "broken glass", "hazard", "trip", "injury", "fainted"],
    "EVENT": ["event", "banner", "carpet", "display", "cord", "rope", "stage", "microphone"],
    "MOVE": ["chair", "table", "furniture", "desk", "sofa", "cabinet"],
    "RENO": ["paint", "wall", "ceiling", "window", "tile", "peeling", "crack"],
    "LAND": ["plant", "tree", "shrub", "garden", "weed", "grass"],
    "LOCK": ["locker", "lock", "padlock"],
    "MAIL": ["mail", "pigeonhole", "parcel", "post"],
    "KEY": ["key", "fob", "key card", "duplicate"],
    "OTHR": [],
}

_clf = None
_clf_error = None


def _classifier():
    """Reuse the project's trained TF-IDF + keyword model (ml/model.pkl)."""
    global _clf, _clf_error
    if _clf is not None:
        return _clf
    try:
        from ..ai import classify as model_classify
        _clf = model_classify
    except Exception as exc:
        _clf = False
        _clf_error = repr(exc)
        print(f"[assistant] model unavailable, keyword fallback: {_clf_error}", flush=True)
    return _clf


def _classify_fallback(text: str):
    low = text.lower()
    hits = {g: sum(1 for w in words if w in low)
            for g, words in _FALLBACK_KEYWORDS.items() if words}
    if not hits:
        return "OTHR", 0.3, {"OTHR": 0.3}
    top = max(hits, key=hits.get)
    return top, min(0.5 + 0.1 * hits[top], 0.9), {top: min(0.5 + 0.1 * hits[top], 0.9)}


def _top3(probs: dict, n: int = 3):
    return sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:n]


def classify(text: str) -> dict:
    """→ {"group", "confidence", "top3", "backend"}"""
    backend = config.CLASSIFY_BACKEND
    if backend == "local_model":
        fn = _classifier()
        if fn:
            group, conf, probs = fn(text)
            return {"group": group, "confidence": round(float(conf), 3),
                    "top3": _top3(probs), "backend": "local_model (ml/model.pkl)"}
        group, conf, probs = _classify_fallback(text)
        return {"group": group, "confidence": round(float(conf), 3),
                "top3": _top3(probs), "backend": "keyword fallback"}
    if backend in ("openai", "ollama"):
        return {"group": _classify_llm(text, backend), "confidence": None,
                "top3": [], "backend": backend}
    group, conf, probs = _classify_fallback(text)
    return {"group": group, "confidence": round(float(conf), 3),
            "top3": _top3(probs), "backend": "mock"}


def _classify_llm(text: str, backend: str) -> str:
    prompt = (
        "You are a facility-management ticket classifier for a university campus. "
        "Classify this report into EXACTLY ONE of these groups: "
        + ", ".join(GROUPS)
        + ". Reply with only the group code.\n\nReport: " + text
    )
    messages = [{"role": "user", "content": prompt}]
    reply = _chat_openai_text(messages) if backend == "openai" else _chat_ollama(messages)
    code = re.sub(r"[^A-Z]", "", reply.strip().upper())
    return code if code in GROUPS else "OTHR"
