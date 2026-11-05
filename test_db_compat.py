# -*- coding: utf-8 -*-
import os, sys, tempfile
sys.path.insert(0, r"C:\Users\yingg\Documents\1.school\openstudio\Project_Code_for_the_Learning_Experience_Enhancement_Initiatives_under_the_LEAD_Scheme")
os.environ["DATABASE_URL"] = ""
from pathlib import Path
from datetime import datetime
import app.db as db

tmp = Path(tempfile.mkdtemp()) / "regress.db"
db.DB_PATH = tmp

db.init_db()
db.seed_lookups()
db.update_zone_labels()
db.update_zone_labels()          # 第二次触发 INSERT OR IGNORE
from app.ai import reseed_categories
print("RESEED_CATEGORIES =", reseed_categories())

conn = db.get_conn()
print("ZONES =", conn.execute("SELECT COUNT(*) FROM zones").fetchone()[0])

now = datetime.now().isoformat()
def mk(code, desc):
    conn.execute("""INSERT INTO tickets (code, zone_code, asset_code, category_code, group_code,
        symptom, impact, priority, status, description, photo_path, reporter_email,
        ai_confidence, ai_predicted, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (code,"YKM-4-CONC-COFFEE","","","HVAC","leaking","SAFETY","P1","NEW",desc,"","",0.5,"HVAC",now,now))

c1 = db.next_ticket_code(); mk(c1, "空调漏水 地面有积水")
conn.commit()
c2 = db.next_ticket_code(); mk(c2, "空调又漏水 积水扩大")
conn.commit()
print("CODES =", c1, c2)

rows = conn.execute("SELECT * FROM tickets WHERE created_at >= datetime('now', ?) ORDER BY created_at DESC", ("-30 days",)).fetchall()
print("RECENT_30D =", len(rows))

cur = conn.execute("INSERT INTO ticket_compilations (title,zone_code,group_code,created_at) VALUES (?,?,?,?) RETURNING id", ("t","YKM-4-CONC-COFFEE","HVAC",now))
cid = cur.fetchone()[0]
conn.execute("INSERT INTO compilation_members (compilation_id,ticket_id,similarity) VALUES (?,?,?)", (cid, 1, 0.9))
conn.commit()
print("COMPILATION_ID =", cid)

conn.execute("INSERT INTO analytics_events (event_name,page,ticket_code,metadata,created_at) VALUES (?,?,?,?,?)", ("report_submitted","reporter",c1,"{}",now))
conn.commit()

t = conn.execute("SELECT * FROM tickets WHERE code=?", (c1,)).fetchone()
print("ROW_BY_NAME =", t["code"], "ROW_BY_INDEX =", t[0])
print("ANALYTICS =", conn.execute("SELECT COUNT(*) FROM analytics_events").fetchone()[0])
conn.close()

print("SQLITE_OK = True")
print("--- Postgres 方言转换 ---")
for s in [
    "SELECT * FROM tickets WHERE created_at >= datetime('now', ?) ORDER BY created_at DESC",
    "INSERT OR IGNORE INTO zones (code,label,building,floor) VALUES (?,?,?,?)",
    "SELECT * FROM tickets WHERE code IN (?,?,?)",
    "UPDATE tickets SET status='ASSIGNED', updated_at=? WHERE id=?",
]:
    print(db._translate(s))
