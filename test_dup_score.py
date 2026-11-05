"""查重评分的快速校验（不启动服务，只验证算法）。"""
import sys

sys.path.insert(0, ".")

from app.main import similarity_score, containment_score, zone_match  # noqa: E402

CASES = [
    (
        "咖啡区空调漏水，地面有积水",
        "YKM 4/F 咖啡车区（Coffee cart area）空调漏水，地面已出现积水，存在滑倒风险，需尽快处理。",
    ),
    (
        "4楼咖啡区空调还在漏水，地上又有积水了",
        "YKM 4/F 咖啡车区（Coffee cart area）空调漏水，地面已出现积水，存在滑倒风险，需尽快处理。",
    ),
    (
        "4楼卫生间洗手池堵了，水已经漫到地面上",
        "YKM 4/F 咖啡车区（Coffee cart area）空调漏水，地面已出现积水，存在滑倒风险，需尽快处理。",
    ),
    (
        "The air conditioner in LT-1 is leaking water onto the floor",
        "YKM 4/F LT1 lecture theatre air conditioner leaking water onto the floor",
    ),
    (
        "灯坏了",
        "YKM 4/F 咖啡车区（Coffee cart area）空调漏水，地面已出现积水，存在滑倒风险，需尽快处理。",
    ),
]

for query, existing in CASES:
    jac = similarity_score(query, existing)
    con = containment_score(query, existing)
    print(f"Jaccard={jac:.3f} containment={con:.3f} final={max(jac, con):.3f}  :: {query}")

print("\nzone_match:")
for a, b in [
    ("YKM-4-CONC-COFFEE", "YKM-4-CONC"),
    ("YKM-4-CONC", "YKM-4-CONC-COFFEE"),
    ("YKM-4-CONC-COFFEE", "YKM-4-CONC-COFFEE"),
    ("YKM-4-CONC-L1", "YKM-4-CONC-COFFEE"),
    ("YKM-3-LAB", "YKM-4-CONC"),
    ("YKM-4-CONC-COFFEE", ""),
]:
    print(f"  {a!r} vs {b!r} -> {zone_match(a, b)}")
