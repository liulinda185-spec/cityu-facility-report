"""LLM 客户端 — 统一接口，支持多种后端，通过环境变量切换（见 config.py）。

对话（CHAT_BACKEND）：     mock | openai | ollama
工单分类（CLASSIFY_BACKEND）：local_model | openai | ollama | mock
"""
from __future__ import annotations

import re

import requests

import config

# ---------------------------------------------------------------- 通用 HTTP


def _http_post(url: str, headers: dict, payload: dict, timeout: int = 60):
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------- 对话


def chat(messages: list[dict]) -> dict:
    """messages: [{"role": "user"/"assistant", "content": "..."}] → {"reply", "backend"}"""
    backend = config.CHAT_BACKEND
    if backend == "openai":
        return {"reply": _chat_openai(messages), "backend": "openai"}
    if backend == "ollama":
        return {"reply": _chat_ollama(messages), "backend": "ollama"}
    return {"reply": _chat_mock(messages), "backend": "mock"}


def _chat_mock(messages: list[dict]) -> str:
    last = [m for m in messages if m.get("role") == "user"]
    text = last[-1]["content"] if last else ""
    return (
        "【模拟模式】已收到你的消息：\n"
        f"「{text}」\n\n"
        "当前未接真实大模型。设置 CHAT_BACKEND=openai（填 API Key）或 =ollama（安装本地模型）后，"
        "这里就会返回真实大模型的回答。"
    )


def _chat_openai(messages: list[dict]) -> str:
    data = _http_post(
        f"{config.OPENAI_BASE_URL}/chat/completions",
        {"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"},
        {"model": config.OPENAI_MODEL, "messages": messages},
    )
    return data["choices"][0]["message"]["content"]


def _chat_ollama(messages: list[dict]) -> str:
    data = _http_post(
        f"{config.OLLAMA_BASE_URL}/api/chat",
        {"Content-Type": "application/json"},
        {"model": config.OLLAMA_MODEL, "messages": messages, "stream": False},
    )
    return data["message"]["content"]


# ---------------------------------------------------------------- 工单分类

# 14 个分组（与现有 FastAPI 系统完全一致）
GROUPS = ["SIGN", "CLEAN", "SECU", "AUTO", "EVENT", "HVAC", "SAFE", "MOVE",
          "RENO", "LAND", "LOCK", "MAIL", "KEY", "OTHR"]

_ai_classify = None
_ai_load_error = None


def _get_ai_classify():
    """复用项目里训练好的模型（app/ai.classify：TF-IDF + 关键词混合，读 ml/model.pkl）。"""
    global _ai_classify, _ai_load_error
    if _ai_classify is not None:
        return _ai_classify
    try:
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent   # 项目根目录（含 app/ 包）
        here = Path(__file__).resolve().parent          # llm_webapp 目录
        # 排除自身目录：本目录的 app.py 会遮蔽项目根的 app 包
        sys.path = [p for p in sys.path if Path(p).resolve() != here]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from app.ai import classify
        _ai_classify = classify
    except Exception as exc:
        _ai_classify = False
        _ai_load_error = repr(exc)
        import sys as _sys
        print(f"[llm_client] 训练模型加载失败，降级为关键词兜底: {_ai_load_error}",
              file=_sys.stderr, flush=True)
    return _ai_classify


# 内置简易关键词（模型不可用时的兜底，避免页面报错）
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


def _classify_fallback(text: str):
    low = text.lower()
    hits = {g: sum(1 for w in words if w in low)
            for g, words in _FALLBACK_KEYWORDS.items() if words}
    if not hits:
        return "OTHR", 0.3, {"OTHR": 0.3}
    top = max(hits, key=hits.get)
    conf = min(0.5 + 0.1 * hits[top], 0.9)
    return top, conf, {top: conf}


def classify(text: str) -> dict:
    """→ {"group", "confidence", "top3", "backend"}"""
    backend = config.CLASSIFY_BACKEND
    if backend == "local_model":
        fn = _get_ai_classify()
        if fn:
            group, conf, probs = fn(text)
            return {"group": group, "confidence": round(float(conf), 3),
                    "top3": _top3(probs), "backend": "local_model (ml/model.pkl)"}
        group, conf, probs = _classify_fallback(text)
        return {"group": group, "confidence": round(float(conf), 3),
                "top3": _top3(probs), "backend": "fallback keywords"}
    if backend in ("openai", "ollama"):
        group = _classify_llm(text, backend)
        return {"group": group, "confidence": None, "top3": [],
                "backend": backend}
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
    reply = _chat_openai(messages) if backend == "openai" else _chat_ollama(messages)
    code = re.sub(r"[^A-Z]", "", reply.strip().upper())
    return code if code in GROUPS else "OTHR"


def _top3(probs: dict, n: int = 3):
    return sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:n]
