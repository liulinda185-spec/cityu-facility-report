"""llm_webapp 配置 — 全部通过环境变量注入，便于切换后端。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 自动读取 llm_webapp/.env（若存在；系统里已设置的环境变量优先，不会被 .env 覆盖）
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

# 对话后端：mock | openai | ollama
CHAT_BACKEND = os.getenv("CHAT_BACKEND", "mock").strip().lower()

# 工单分类后端：local_model | openai | ollama | mock
CLASSIFY_BACKEND = os.getenv("CLASSIFY_BACKEND", "local_model").strip().lower()

# OpenAI 兼容 API（DeepSeek / 豆包火山方舟 / OpenAI 等均可）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Ollama 本地模型
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

# 主系统 API（工单助手工具调用目标：创建工单 / 查重 / 追踪）
FACILITY_API_BASE = os.getenv("FACILITY_API_BASE", "https://cityu-facility-report.onrender.com").rstrip("/")
# 主管令牌：查重 /api/duplicates 等受保护接口需要（与主系统 OFFICER_TOKEN 相同）
FACILITY_TOKEN = os.getenv("FACILITY_TOKEN", "")

# 训练好的 sklearn 模型路径（默认指向项目 ml/model.pkl）
MODEL_PATH = Path(os.getenv("MODEL_PATH", str(BASE_DIR.parent / "ml" / "model.pkl")))

# Web 端口：云平台注入的 $PORT 优先，其次 LLM_WEBAPP_PORT，最后默认 5050
PORT = int(os.getenv("PORT") or os.getenv("LLM_WEBAPP_PORT") or "5050")
