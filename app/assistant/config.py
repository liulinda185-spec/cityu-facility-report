"""Assistant settings — environment only, no .env file, no secret in the repo."""
from __future__ import annotations

import os

CHAT_BACKEND = os.getenv("CHAT_BACKEND", "mock").strip().lower()
CLASSIFY_BACKEND = os.getenv("CLASSIFY_BACKEND", "local_model").strip().lower()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")


def llm_ready() -> bool:
    """True only when a real chat backend is configured with a credential."""
    if CHAT_BACKEND == "openai":
        return bool(OPENAI_API_KEY)
    if CHAT_BACKEND == "ollama":
        return True
    return False
