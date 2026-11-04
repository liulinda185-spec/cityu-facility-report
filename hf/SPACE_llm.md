---
title: CityU Facility LLM Demo
emoji: 🤖
colorFrom: teal
colorTo: emerald
sdk: docker
app_port: 7860
pinned: false
---

# CityU Facility LLM Demo

Flask demo for the LEAD Scheme: DeepSeek chat + AI ticket classification using the local trained model (`ml/model.pkl`).

- `/api/health` — service status
- Chat tab — DeepSeek conversation (needs `OPENAI_API_KEY` secret)
- Classify tab — ticket category prediction (local model)

Set `OPENAI_API_KEY` under **Settings → Variables and secrets** before using the chat.
