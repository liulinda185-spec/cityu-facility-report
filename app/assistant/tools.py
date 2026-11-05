"""Tickets assistant tools — they call this same app, not a second deployment.

`Api` is a minimal self-client: base URL comes from the incoming request, so it
works on localhost:8013, on Render, or behind any proxy without configuration.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


class Api:
    def __init__(self, base_url: str, token: str | None = None):
        self.base = base_url.rstrip("/")
        self.token = token if token is not None else os.getenv("OFFICER_TOKEN", "")
        self.calls: list[str] = []

    def _open(self, path: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 30):
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data else "GET")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def get(self, path: str, params: dict | None = None):
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        status, body = self._open(path + query)
        if status != 200:
            raise RuntimeError(f"{path} → HTTP {status}: {body[:200]!r}")
        return json.loads(body.decode("utf-8"))

    def post_form(self, path: str, data: dict):
        status, body = self._open(
            path, urllib.parse.urlencode(data).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        if status != 200:
            raise RuntimeError(f"{path} → HTTP {status}: {body[:200]!r}")
        return json.loads(body.decode("utf-8"))


def create_ticket(args: dict, api: Api) -> dict:
    payload = {
        "zone_code": str(args.get("zone_code") or ""),
        "asset_code": str(args.get("asset_code") or ""),
        "symptom": str(args.get("symptom") or ""),
        "impact": str(args.get("impact") or "P3"),
        "description": str(args.get("description") or ""),
        "reporter_email": str(args.get("reporter_email") or ""),
        "category_code": str(args.get("category_code") or ""),
    }
    out = api.post_form("/api/reports", payload)
    return {"ok": True, "code": out.get("code"), "group": out.get("group"),
            "confidence": out.get("confidence")}


def find_duplicates(args: dict, api: Api) -> dict:
    if not api.token:
        return {"count": 0, "matches": [], "error": "未配置 OFFICER_TOKEN，无法查重"}
    params = {
        "token": api.token,
        "threshold": args.get("threshold", 0.34),
        "days": args.get("days", 30),
        "query_text": args.get("description", ""),
        "query_zone": args.get("zone_code", ""),
    }
    data = api.get("/api/duplicates", params)
    if isinstance(data, dict) and "matches" in data:
        matches = [{
            "code": m["ticket"]["code"], "status": m["ticket"]["status"],
            "zone_code": m["ticket"]["zone_code"], "group_code": m["ticket"]["group_code"],
            "description": m["ticket"]["description"], "similarity": m["similarity"],
        } for m in data["matches"]]
        return {"count": len(matches), "matches": matches, "clusters": []}
    return {"count": len(data) if isinstance(data, list) else 0, "matches": [], "clusters": data}


def track_ticket(args: dict, api: Api) -> dict:
    code = str(args.get("code") or "")
    if not code:
        return {"error": "缺少工单编号 code"}
    try:
        data = api.get(f"/api/reports/{urllib.parse.quote(code)}")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return {"error": f"工单 {code} 不存在"}
        raise
    t = data.get("ticket", {})
    return {"code": t.get("code"), "status": t.get("status"), "group": t.get("group_code"),
            "priority": t.get("priority"),
            "description": (t.get("description") or "")[:120],
            "updated_at": t.get("updated_at")}


def get_zones(args: dict, api: Api) -> dict:
    zones = api.get("/api/zones")
    return {"zones": [{"code": z["code"], "label": z["label"]} for z in zones]}


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

_IMPL = {
    "create_ticket": create_ticket,
    "find_duplicates": find_duplicates,
    "track_ticket": track_ticket,
    "get_zones": get_zones,
}


def execute(name: str, args: dict, api: Api) -> dict:
    fn = _IMPL.get(name)
    if fn is None:
        raise ValueError(f"未知工具: {name}")
    return fn(args, api)
