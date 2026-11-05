"""工单助手工具：通过主系统 API 创建/查重/追踪工单，并上报埋点事件。

工具被 llm_client.assistant 的 function calling 循环调用；
埋点走主系统 POST /api/analytics（fire-and-forget，失败不影响主流程）。
"""
from __future__ import annotations

import requests

import config

# ---------------------------------------------------------------- 埋点

_EVENT_WHITELIST = {
    "assistant_opened", "assistant_message", "assistant_tool_call",
    "assistant_ticket_created", "assistant_duplicates_found", "assistant_error",
}


def track_event(event: str, page: str = "assistant", metadata: dict | None = None):
    if event not in _EVENT_WHITELIST:
        return
    try:
        requests.post(
            f"{config.FACILITY_API_BASE}/api/analytics",
            json={"event": event, "page": page, "metadata": metadata or {}},
            timeout=5,
        )
    except Exception:
        pass  # 埋点失败不阻断主流程


# ---------------------------------------------------------------- 工具实现


def create_ticket(args: dict) -> dict:
    """按 LLM 提取的字段在主系统创建工单 → {"code","group","confidence"}"""
    payload = {
        "zone_code": str(args.get("zone_code") or ""),
        "asset_code": str(args.get("asset_code") or ""),
        "symptom": str(args.get("symptom") or ""),
        "impact": str(args.get("impact") or "P3"),
        "description": str(args.get("description") or ""),
        "reporter_email": str(args.get("reporter_email") or ""),
        "category_code": str(args.get("category_code") or ""),
    }
    resp = requests.post(f"{config.FACILITY_API_BASE}/api/reports", data=payload, timeout=30)
    resp.raise_for_status()
    out = resp.json()
    track_event("assistant_ticket_created",
                metadata={"code": out.get("code"), "group": out.get("group")})
    return {"ok": True, "code": out.get("code"), "group": out.get("group"),
            "confidence": out.get("confidence")}


def find_duplicates(args: dict) -> dict:
    """查重复：预检模式（新描述 vs 库内工单）+ 库内聚类，→ {"count", "matches", "clusters"}"""
    if not config.FACILITY_TOKEN:
        return {"count": 0, "matches": [], "error": "未配置 FACILITY_TOKEN，无法查重"}
    params = {
        "token": config.FACILITY_TOKEN,
        "threshold": args.get("threshold", 0.34),
        "days": args.get("days", 30),
        "query_text": args.get("description", ""),
        "query_zone": args.get("zone_code", ""),
    }
    resp = requests.get(f"{config.FACILITY_API_BASE}/api/duplicates", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "matches" in data:
        matches = data["matches"]
        simplified = [{
            "code": m["ticket"]["code"], "status": m["ticket"]["status"],
            "zone_code": m["ticket"]["zone_code"], "group_code": m["ticket"]["group_code"],
            "description": m["ticket"]["description"], "similarity": m["similarity"],
        } for m in matches]
        track_event("assistant_duplicates_found", metadata={"matches": len(simplified)})
        return {"count": len(simplified), "matches": simplified, "clusters": []}
    clusters = data if isinstance(data, list) else []
    track_event("assistant_duplicates_found", metadata={"clusters": len(clusters)})
    return {"count": len(clusters), "matches": [], "clusters": clusters}


def track_ticket(args: dict) -> dict:
    """按工单编号查进度 → {"code","status","group","priority","description"}"""
    code = str(args.get("code") or "")
    if not code:
        return {"error": "缺少工单编号 code"}
    resp = requests.get(f"{config.FACILITY_API_BASE}/api/reports/{code}", timeout=30)
    if resp.status_code == 404:
        return {"error": f"工单 {code} 不存在"}
    resp.raise_for_status()
    t = resp.json().get("ticket", {})
    return {"code": t.get("code"), "status": t.get("status"), "group": t.get("group_code"),
            "priority": t.get("priority"),
            "description": (t.get("description") or "")[:120],
            "updated_at": t.get("updated_at")}


def get_zones(args: dict) -> dict:
    """列出可用位置代码 → {"zones": [{"code","label"}]}"""
    resp = requests.get(f"{config.FACILITY_API_BASE}/api/zones", timeout=30)
    resp.raise_for_status()
    zones = resp.json()
    return {"zones": [{"code": z["code"], "label": z["label"]} for z in zones]}


# ---------------------------------------------------------------- 工具定义（OpenAI function calling schema）


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


TOOL_DEFS = [
    _tool(
        "create_ticket",
        "创建一张新的设施报修工单。信息齐全时调用；创建前应先调用 find_duplicates 查重。",
        {
            "zone_code": {"type": "string", "description": "位置代码，如 YKM-4-CONC-L1；不知道可先调 get_zones"},
            "description": {"type": "string", "description": "问题描述（必填，尽量详细）"},
            "symptom": {"type": "string", "description": "症状关键词，如 not cooling / leaking"},
            "impact": {"type": "string", "enum": ["BLOCKED", "SAFETY", "EMERGENCY", "MINOR", "INCONVENIENCE"], "description": "影响程度，默认 INCONVENIENCE"},
            "asset_code": {"type": "string", "description": "资产编号（如有）"},
            "reporter_email": {"type": "string", "description": "报障人邮箱（如有）"},
        },
        ["zone_code", "description"],
    ),
    _tool(
        "find_duplicates",
        "创建工单前必须调用：检查该位置是否已有相似的报修工单（预检），避免重复创建。返回相似工单列表；若 count>0 应告知用户并建议合并，不要创建新工单。",
        {
            "description": {"type": "string", "description": "本次问题描述（必填）"},
            "zone_code": {"type": "string", "description": "位置代码，如 YKM-4-CONC；与 create_ticket 的 zone_code 一致"},
            "threshold": {"type": "number", "description": "相似度阈值 0.1~1.0，默认 0.34"},
            "days": {"type": "integer", "description": "回溯天数，默认 30"},
        },
        ["description"],
    ),
    _tool(
        "track_ticket",
        "按工单编号查询处理进度与状态。",
        {"code": {"type": "string", "description": "工单编号，如 FM-2026-000001"}},
        ["code"],
    ),
    _tool(
        "get_zones",
        "列出全部可用位置代码与名称。用户没说具体位置时调用，帮助确认位置。",
        {},
        [],
    ),
]

_TOOL_IMPL = {
    "create_ticket": create_ticket,
    "find_duplicates": find_duplicates,
    "track_ticket": track_ticket,
    "get_zones": get_zones,
}


def execute_tool(name: str, args: dict) -> dict:
    fn = _TOOL_IMPL.get(name)
    if fn is None:
        raise ValueError(f"未知工具: {name}")
    return fn(args)
