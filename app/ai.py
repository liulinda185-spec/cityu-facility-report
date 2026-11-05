"""AI classifier — TF-IDF char n-grams + LogisticRegression.
Loads ml/model.pkl if present; otherwise falls back to keyword rules."""
import joblib, re
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent.parent / "ml" / "model.pkl"

# Keyword fallback (cold start before model is trained)
KEYWORDS = {
    "SIGN":   ["sign", "wayfind", "poster", "direction", "exit sign", "arrow"],
    "CLEAN":  ["clean", "dirty", "smell", "sticky", "spill", "bin", "trash", "vomit", "stain", "litter"],
    "SECU":   ["security", "loiter", "alarm", "suspicious", "graffiti", "cctv", "tailgat"],
    "AUTO":   ["auto door", "automatic door", "sliding door", "entrance door", "door sensor",
               "door won't open", "door wont open", "door not opening", "door not closing",
               "sensor not detecting", "slams shut", "door beeping", "door grinding",
               "door stuck", "door jammed"],
    "HVAC":   ["aircon", "air con", "light", "flicker", "bulb", "temperature", "cold", "hot", "leak", "water", "plug", "socket", "power", "tap", "toilet", "fan", "vent"],
    "SAFE":   ["first aid", "broken glass", "hazard", "trip", "sharp", "extinguisher", "incident", "injury", "fainted"],
    "EVENT":  ["event", "banner", "backdrop", "carpet", "display", "cord", "rope", "stand", "booth", "stage", "microphone"],
    "MOVE":   ["chair", "table", "furniture", "desk", "sofa", "bookcase", "cabinet", "lectern", "podium", "wobbly", "broken furniture"],
    "RENO":   ["paint", "wall", "ceiling", "door frame", "door handle", "window", "tile", "renovat", "peeling", "crack", "blind", "curtain"],
    "LAND":   ["plant", "tree", "shrub", "planter", "garden", "weed", "grass", "leaf", "landscape"],
    "LOCK":   ["locker", "lock", "combination", "padlock"],
    "MAIL":   ["mail", "pigeonhole", "parcel", "post"],
    "KEY":    ["key", "key cut", "fob", "key card", "keycard", "duplicate key"],
    "OTHR":   [],
}

_model = None
def _load():
    global _model
    if _model is None and MODEL_PATH.exists():
        _model = joblib.load(MODEL_PATH)
    return _model

def classify(text: str, symptom: str = "", asset_code: str = ""):
    """Return (group_code, confidence, all_probs_dict).

    Scoring is a hybrid: the trained model's probability distribution is blended
    with keyword evidence. Real reports are much shorter than seed sentences, so
    the pure model output is badly under-confident on 5-word inputs; the keyword
    prior corrects that without inventing numbers.
    """
    combined = f"{text} {symptom}".strip()

    # 1. Asset code prefix is the strongest signal
    if asset_code:
        prefix_map = {
            "AD": "AUTO", "ACU": "HVAC", "LIGHT": "HVAC", "EL": "SECU",
            "SIGN": "SIGN", "TOIL": "HVAC", "FB": "HVAC", "LT": "EVENT",
        }
        m = re.match(r"^([A-Z]+)", asset_code.upper())
        if m and m.group(1) in prefix_map:
            return prefix_map[m.group(1)], 0.99, {prefix_map[m.group(1)]: 0.99}

    text_lower = combined.lower()

    # 2. Keyword evidence as a distribution. Generic terms (light, water, power,
    # door…) appear in almost every complaint and used to drag unrelated tickets
    # into HVAC; they now count 0.3, specific terms count 1.0.
    GENERIC_WORDS = {
        "light", "water", "tap", "toilet", "power", "fan", "vent",
        "cold", "hot", "leak", "plug", "socket", "door", "sensor",
        "wall", "ceiling", "window", "tile", "cord", "poster",
    }
    kw_hits = {}
    for group, words in KEYWORDS.items():
        s = 0.0
        for w in words:
            if w in text_lower:
                s += 0.3 if w in GENERIC_WORDS else 1.0
        if s:
            kw_hits[group] = s
    kw_total = sum(kw_hits.values())

    # 3. Trained model distribution
    model = _load()
    probs = {}
    if model is not None:
        raw = model.predict_proba([combined])[0]
        probs = {c: float(p) for c, p in zip(model.classes_, raw)}

    # 4. Blend
    if probs and kw_total:
        merged = {}
        for g in set(list(probs.keys()) + list(kw_hits.keys())):
            merge_p = 0.65 * probs.get(g, 0.0) + 0.35 * (kw_hits.get(g, 0) / kw_total)
            merged[g] = merge_p
    elif probs:
        merged = probs
    elif kw_total:
        merged = {g: 1.0 if g == max(kw_hits, key=kw_hits.get) else 0.0
                  for g in kw_hits}
        top_g = max(kw_hits, key=kw_hits.get)
        merged[top_g] = min(0.5 + 0.1 * kw_hits[top_g], 0.9)
    else:
        return "OTHR", 0.3, {"OTHR": 0.3}

    top_g = max(merged, key=merged.get)
    return top_g, float(merged[top_g]), merged

def suggest_categories(group_code: str):
    """Return list of portal cards belonging to this AI group."""
    from .db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT code, portal_name, type FROM categories WHERE group_code = ? "
        "ORDER BY CASE type WHEN 'problem' THEN 1 WHEN 'request' THEN 2 ELSE 3 END, portal_name",
        (group_code,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def reseed_categories():
    """Rebuild the categories table from categories.csv, keeping tickets intact."""
    import csv
    from .db import get_conn, BASE_DIR
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM categories")
    with open(BASE_DIR / "categories.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cur.execute(
                "INSERT INTO categories (code,portal_name,type,team_email,group_code) VALUES (?,?,?,?,?)",
                (row["code"], row["portal_name"], row["type"],
                 row["team_email_placeholder"], row["group_code"])
            )
    conn.commit()
    n = cur.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    conn.close()
    return n
