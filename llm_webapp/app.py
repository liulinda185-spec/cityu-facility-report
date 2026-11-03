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


if __name__ == "__main__":
    # 云端部署（Render/Railway 等）会注入 $PORT；本地无该变量时回退到 config.PORT（5050）
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
