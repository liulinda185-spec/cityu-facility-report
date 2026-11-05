# -*- coding: utf-8 -*-
"""SQLite 分支的 HTTP 全流程冒烟（本地 8012）。"""
import json
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8012"
TOKEN = "testtoken123"
ok = []
fail = []


def check(name, cond, detail=""):
    (ok if cond else fail).append(f"{name}{(' :: ' + str(detail)) if detail else ''}")
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def get(path, **q):
    q["token"] = TOKEN
    url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
    with urllib.request.urlopen(url, timeout=25) as r:
        return r.status, json.loads(r.read().decode())


def post(path, data, with_token=True):
    body = urllib.parse.urlencode(data, doseq=True).encode()
    url = f"{BASE}{path}" + (f"?token={TOKEN}" if with_token else "")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status, json.loads(r.read().decode())


# 1 首页
with urllib.request.urlopen(f"{BASE}/", timeout=25) as r:
    html = r.read().decode("utf-8", "replace")
check("GET / 200", r.status == 200, r.status)
check("首页含报修表单", "zone_code" in html and "description" in html, len(html))

# 2 字典
s, d = get("/api/zones")
check("GET /api/zones", s == 200 and len(d) == 23, len(d))
s, d = get("/api/category-options")
check("GET /api/category-options", s == 200 and len(d) >= 25, len(d))
s, d = get("/api/suggest", group_code="HVAC")
check("GET /api/suggest", s == 200 and len(d) > 0, len(d))

# 3 建单（两条，同区，中文描述）
codes = []
for desc in ["空调漏水 地面有积水 很危险", "空调又漏水了 积水扩大到走廊"]:
    s, d = post("/api/reports", {
        "zone_code": "YKM-4-CONC-COFFEE",
        "asset_code": "",
        "symptom": "leak",
        "impact": "SAFETY",
        "description": desc,
        "reporter_email": "",
        "category_code": "",
    }, with_token=False)
    check(f"POST /api/reports ({desc[:8]})", s == 200 and d.get("code"), d)
    codes.append(d["code"])

# 4 追踪
s, d = get(f"/api/reports/{codes[0]}")
check("GET /api/reports/{code}", s == 200 and d["ticket"]["status"] == "NEW", d["ticket"]["status"])
check("status_events 已写入", len(d["events"]) == 1, len(d["events"]))

# 5 查重预检（datetime('now', ?) 路径）
s, d = get("/api/duplicates", query_text="空调漏水 积水", query_zone="YKM-4-CONC-COFFEE", days=30)
check("查重预检中文命中", isinstance(d, dict) and len(d.get("matches", [])) >= 1,
      d.get("matches", [{}])[0].get("similarity") if d.get("matches") else 0)
s, d2 = get("/api/duplicates", query_text="打印机卡纸 无法使用", query_zone="YKM-4-CONC-COFFEE", days=30)
check("查重预检对照不命中", len(d2.get("matches", [])) == 0, len(d2.get("matches", [])))

# 6 10 天窗口（另一个 days 值）
s, d3 = get("/api/duplicates", query_text="空调漏水", query_zone="YKM-4-CONC-COFFEE", days=7)
check("查重 days=7", isinstance(d3, dict), len(d3.get("matches", [])))

# 7 聚类模式（无 query_text，返回列表）
s, dc = get("/api/duplicates", days=30)
check("查重聚类模式返回列表", isinstance(dc, list), type(dc).__name__)

# 8 合并（RETURNING id 路径）
s, d = post("/api/compilations", {"token": TOKEN, "ticket_codes": [codes[0], codes[1]]})
check("POST /api/compilations (RETURNING id)", s == 200 and d.get("compilation_id"), d)

# 9 队列
s, q = get("/api/queue", status="NEW")
check("GET /api/queue", s == 200 and len(q) >= 2, len(q))
qrow = next(x for x in q if x["code"] == codes[0])
check("queue 带 compilation_id", qrow.get("compilation_id") is not None, qrow.get("compiled_count"))

# 10 认领
s, d = post(f"/api/queue/{codes[0]}/claim", {"token": TOKEN})
check("POST claim", s == 200 and d.get("ok"), d)
s, d = get(f"/api/reports/{codes[0]}")
check("认领后状态 ASSIGNED", d["ticket"]["status"] == "ASSIGNED", d["ticket"]["status"])

# 11 改分类（training_examples 写入）
s, d = post(f"/api/queue/{codes[0]}/recategorize", {"token": TOKEN, "new_category": "HVAC-AHU"})
check("POST recategorize", s == 200, d)

# 12 解决
s, d = post(f"/api/queue/{codes[0]}/resolve", {"token": TOKEN})
check("POST resolve", s == 200 and d.get("ok"), d)
s, d = get(f"/api/reports/{codes[0]}")
check("解决后状态 RESOLVED", d["ticket"]["status"] == "RESOLVED", d["ticket"]["status"])

def post_json(path, obj):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(obj).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status, json.loads(r.read().decode())


# 13 埋点
s, d = post_json("/api/analytics", {"event": "report_submitted", "page": "reporter",
                                   "ticket_code": codes[0], "metadata": {"priority": "P1", "group": "HVAC"}})
check("POST /api/analytics", s == 200 and d.get("ok"), d)

# 14 越权
try:
    urllib.request.urlopen(f"{BASE}/api/queue?token=wrong", timeout=20)
    check("错误 token 应 403", False, 200)
except urllib.error.HTTPError as e:
    check("错误 token 应 403", e.code == 403, e.code)

print("\n=== SUMMARY ===")
print(f"PASS={len(ok)}  FAIL={len(fail)}")
for f in fail:
    print("  FAILED:", f)
