"""CityU Facility 报修系统 — LLM 演示 Web 应用（Flask）

运行:  python app.py   →  http://127.0.0.1:5050
"""
from flask import Flask, jsonify, render_template, request

import config
import llm_client

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True  # 开发期改动模板无需重启


@app.get("/")
def index():
    return render_template(
        "index.html",
        chat_backend=config.CHAT_BACKEND,
        classify_backend=config.CLASSIFY_BACKEND,
    )


@app.get("/api/health")
def health():
    return jsonify(
        ok=True,
        chat_backend=config.CHAT_BACKEND,
        classify_backend=config.CLASSIFY_BACKEND,
    )


@app.post("/api/chat")
def api_chat():
    data = request.get_json(force=True, silent=True) or {}
    messages = data.get("messages") or []
    if not messages or not any(m.get("content") for m in messages):
        return jsonify(error="messages 不能为空"), 400
    try:
        return jsonify(llm_client.chat(messages))
    except Exception as exc:
        return jsonify(error=f"调用后端失败: {exc}"), 502


@app.post("/api/classify")
def api_classify():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="text 不能为空"), 400
    try:
        return jsonify(llm_client.classify(text))
    except Exception as exc:
        return jsonify(error=f"分类失败: {exc}"), 502


@app.post("/api/analytics")
def api_analytics_forward():
    """前端埋点转发：写入主系统 analytics（白名单校验在 assistant_tools.track_event 内）。"""
    from assistant_tools import track_event
    data = request.get_json(force=True, silent=True) or {}
    track_event(
        str(data.get("event") or ""),
        page=str(data.get("page") or "assistant"),
        metadata=data.get("metadata") or {},
    )
    return jsonify(ok=True)


@app.post("/api/assistant")
def api_assistant():
    """工单助手：system prompt + function calling，可创建/查重/追踪工单。"""
    from assistant_tools import track_event

    data = request.get_json(force=True, silent=True) or {}
    messages = data.get("messages") or []
    image_url = (data.get("image_url") or "").strip()  # 预留视觉通道
    if not messages or not any(m.get("content") for m in messages):
        return jsonify(error="messages 不能为空"), 400
    track_event("assistant_message", page="assistant",
                metadata={"turns": len(messages)})
    try:
        return jsonify(llm_client.assistant(messages, image_url=image_url))
    except Exception as exc:
        track_event("assistant_error", page="assistant",
                    metadata={"error": str(exc)[:200]})
        return jsonify(error=f"工单助手调用失败: {exc}"), 502


if __name__ == "__main__":
    # 云端部署（Render/Railway 等）会注入 $PORT；本地无该变量时回退到 config.PORT（5050）
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
