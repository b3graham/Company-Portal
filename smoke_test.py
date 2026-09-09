"""Throwaway smoke test for the expenses module. Run:  python smoke_test.py"""
import io
import os
import shutil
import struct
import tempfile
import zlib

tmp = tempfile.mkdtemp(prefix="portal-test-")
os.environ["DATA_DIR"] = tmp

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app import db  # noqa: E402

db.init()
client = TestClient(app)
failures = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def png_bytes():
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    ihdr = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
    raw = zlib.compress(b"".join(b"\x00" + b"\x80\x80\x80" * 4 for _ in range(4)))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


# first-run setup creates the admin
r = client.post("/setup", data={"name": "Earl", "email": "earl@stone-knight.com",
                                "password": "testpass123"}, follow_redirects=False)
check("setup", r.status_code == 303, r.text[:200])

for path in ("/expenses", "/expenses/mileage", "/expenses/dashboard", "/expenses/settings"):
    r = client.get(path)
    check(f"GET {path}", r.status_code == 200, r.text[:300])

# seeded lists show up
r = client.get("/expenses")
for name in ("Stone-Knight", "Rustic Saw", "National Guard", "Fuel", "Business Card"):
    check(f"seeded: {name}", name in r.text)
r = client.get("/expenses/mileage")
for name in ("2025 F-150", "1996 F-150"):
    check(f"seeded: {name}", name in r.text)

# ids of seeded items
ent = db.query_one("SELECT id FROM expense_lists WHERE kind='entity' AND name='Stone-Knight'")["id"]
cat = db.query_one("SELECT id FROM expense_lists WHERE kind='category' AND name='Fuel'")["id"]
pay = db.query_one("SELECT id FROM expense_lists WHERE kind='payment' AND name='Business Card'")["id"]
veh = db.query_one("SELECT id FROM expense_lists WHERE kind='vehicle' AND name=\"2025 F-150\"")["id"]

# add expense with a receipt
r = client.post("/expenses/add",
                data={"date": "2026-08-05", "entity_id": ent, "merchant": "Tractor Supply",
                      "category_id": cat, "payment_id": pay, "amount": "45.67",
                      "tags": "field, gear", "notes": "chain oil"},
                files={"receipt": ("receipt.png", io.BytesIO(png_bytes()), "image/png")},
                follow_redirects=False)
check("add expense w/ receipt", r.status_code == 303, r.text[:300])
r = client.post("/expenses/add",
                data={"date": "2026-07-14", "entity_id": ent, "merchant": "Shell",
                      "category_id": cat, "amount": "$1,082.10"},
                follow_redirects=False)
check("add expense $1,082.10", r.status_code == 303, r.text[:300])
check("bad amount rejected",
      client.post("/expenses/add", data={"date": "2026-08-05", "entity_id": ent,
                                         "merchant": "x", "amount": "abc"}).status_code == 422)

r = client.get("/expenses?range=all")
check("expense listed", "Tractor Supply" in r.text and "$45.67" in r.text)
check("total = $1,127.77", "$1,127.77" in r.text, r.text[:100])
check("receipt link shown", "/receipt" in r.text)
check("search filters", "Shell" not in client.get("/expenses?range=all&q=Tractor").text)
check("month filter excludes July", "Shell" not in client.get("/expenses?start=2026-08-01&end=2026-08-31").text)

eid = db.query_one("SELECT id FROM expenses WHERE merchant='Tractor Supply'")["id"]
r = client.get(f"/expenses/entry/{eid}/receipt")
check("receipt downloads", r.status_code == 200 and r.content.startswith(b"\x89PNG"))
r = client.get(f"/expenses/entry/{eid}/edit")
check("edit form", r.status_code == 200 and "Tractor Supply" in r.text)
r = client.post(f"/expenses/entry/{eid}/edit",
                data={"date": "2026-08-05", "entity_id": ent, "merchant": "Tractor Supply Co",
                      "category_id": cat, "payment_id": pay, "amount": "50.00",
                      "tags": "field", "notes": ""}, follow_redirects=False)
check("edit expense keeps receipt", r.status_code == 303
      and db.query_one("SELECT receipt_name FROM expenses WHERE id=?", (eid,))["receipt_name"] != "")

# mileage
r = client.post("/expenses/mileage/add",
                data={"date": "2026-08-03", "entity_id": ent, "vehicle_id": veh,
                      "from_place": "Home", "to_place": "Client site", "miles": "23.5",
                      "round_trip": "1", "notes": "site survey"}, follow_redirects=False)
check("add round trip", r.status_code == 303, r.text[:300])
check("round trip doubled to 47.0",
      db.query_one("SELECT miles FROM mileage_trips")["miles"] == 47.0)
r = client.get("/expenses/mileage?range=all")
check("trip listed", "Client site" in r.text and "47.0" in r.text)
check("saved places datalist", "Home" in r.text and "datalist" in r.text)

tid = db.query_one("SELECT id FROM mileage_trips")["id"]
r = client.post(f"/expenses/mileage/{tid}/edit",
                data={"date": "2026-08-03", "entity_id": ent, "vehicle_id": veh,
                      "from_place": "Home", "to_place": "Client site", "miles": "48.2",
                      "notes": ""}, follow_redirects=False)
check("edit trip", r.status_code == 303
      and db.query_one("SELECT miles FROM mileage_trips")["miles"] == 48.2)

# dashboard with data
r = client.get("/expenses/dashboard")
check("dashboard renders with data", r.status_code == 200 and "Monthly spend" in r.text, r.text[:300])
r = client.get(f"/expenses/dashboard?entity={ent}")
check("dashboard entity filter", r.status_code == 200)

# settings
r = client.post("/expenses/settings/category/add", data={"name": "Ammo"}, follow_redirects=False)
check("add category", r.status_code == 303)
new_id = db.query_one("SELECT id FROM expense_lists WHERE name='Ammo'")["id"]
check("rename", client.post(f"/expenses/settings/item/{new_id}/rename",
                            data={"name": "Range fees"}, follow_redirects=False).status_code == 303)
check("toggle", client.post(f"/expenses/settings/item/{new_id}/toggle",
                            follow_redirects=False).status_code == 303)
check("delete unused", client.post(f"/expenses/settings/item/{new_id}/delete",
                                   follow_redirects=False).status_code == 303
      and not db.query_one("SELECT 1 FROM expense_lists WHERE id=?", (new_id,)))
r = client.post(f"/expenses/settings/item/{cat}/delete", follow_redirects=False)
check("in-use delete blocked", r.status_code == 303 and "In+use" in r.headers["location"])

# exports
r = client.get("/expenses/export/expenses.csv?range=all")
check("expenses CSV", r.status_code == 200 and "Tractor Supply Co" in r.text and "50.00" in r.text)
r = client.get("/expenses/export/mileage.csv?range=all")
check("mileage CSV", r.status_code == 200 and "48.2" in r.text)
r = client.get("/expenses/report.pdf?range=all")
check("PDF report", r.status_code == 200 and r.content.startswith(b"%PDF"), r.text[:300])

# ---- CRM module ----
r = client.get("/crm", follow_redirects=False)
check("/crm redirects to leads", r.status_code == 303 and r.headers["location"] == "/crm/leads")
for path in ("/crm/leads", "/crm/contacts", "/crm/projects"):
    check(f"GET {path}", client.get(path).status_code == 200)

r = client.post("/crm/contacts/add",
                data={"name": "Jane Doe", "company": "Acme Corp", "title": "CISO",
                      "email": "jane@acme.com", "phone": "555-0100", "notes": "met at conf"},
                follow_redirects=False)
check("add contact", r.status_code == 303)
cid = db.query_one("SELECT id FROM contacts WHERE name='Jane Doe'")["id"]
check("contact search", "Jane Doe" in client.get("/crm/contacts?q=acme").text)
check("contact search misses", "Jane Doe" not in client.get("/crm/contacts?q=zzz").text)

r = client.post("/crm/leads/add",
                data={"title": "Acme pentest", "entity_id": ent, "contact_id": cid,
                      "source": "Referral", "follow_up": "2026-08-01", "notes": "scope call"},
                follow_redirects=False)
check("add lead", r.status_code == 303)
lid = db.query_one("SELECT id FROM leads WHERE title='Acme pentest'")["id"]
r = client.get("/crm/leads")
check("lead on board", "Acme pentest" in r.text and "Jane Doe" in r.text)
check("overdue follow-up flagged", "overdue" in r.text)

check("advance stage", client.post(f"/crm/leads/{lid}/stage", data={"stage": "contacted"},
                                   follow_redirects=False).status_code == 303
      and db.query_one("SELECT stage FROM leads WHERE id=?", (lid,))["stage"] == "contacted")
check("bad stage rejected", client.post(f"/crm/leads/{lid}/stage",
                                        data={"stage": "nope"}).status_code == 422)

client.post(f"/crm/leads/{lid}/stage", data={"stage": "won"})
row = db.query_one("SELECT stage, closed_at FROM leads WHERE id=?", (lid,))
check("won sets closed_at", row["stage"] == "won" and row["closed_at"])
check("closed lead shown", "Convert to project" in client.get("/crm/leads").text)

r = client.post(f"/crm/leads/{lid}/convert", follow_redirects=False)
proj = db.query_one("SELECT * FROM projects WHERE lead_id=?", (lid,))
check("convert to project", r.status_code == 303 and proj
      and proj["name"] == "Acme pentest" and proj["contact_id"] == cid
      and proj["entity_id"] == ent)
r = client.post(f"/crm/leads/{lid}/convert", follow_redirects=False)
check("double convert blocked", "Already+converted" in r.headers["location"])

pid = proj["id"]
check("project on board", "Acme pentest" in client.get("/crm/projects").text)
client.post(f"/crm/projects/{pid}/move", data={"direction": "next"})
check("project move", db.query_one("SELECT status FROM projects WHERE id=?", (pid,))["status"] == "active")
r = client.post(f"/crm/projects/{pid}/edit",
                data={"name": "Acme pentest Q3", "status": "done", "entity_id": ent,
                      "contact_id": cid, "due_date": "2026-09-30", "notes": ""},
                follow_redirects=False)
check("project edit", r.status_code == 303
      and db.query_one("SELECT name, status FROM projects WHERE id=?", (pid,))["status"] == "done")

r = client.post("/crm/projects/add", data={"name": "Standalone project"}, follow_redirects=False)
check("add standalone project", r.status_code == 303)

check("contact page shows links", "Acme pentest" in client.get(f"/crm/contacts/{cid}/edit").text)
client.post(f"/crm/contacts/{cid}/delete")
check("contact delete keeps lead", db.query_one("SELECT contact_id FROM leads WHERE id=?", (lid,))["contact_id"] is None)
check("contact delete keeps project", db.query_one("SELECT 1 FROM projects WHERE id=?", (pid,)))

# ---- daily challenge ----
n_ch = db.query_one("SELECT COUNT(*) AS n FROM challenges")["n"]
check("challenge bank seeded", n_ch >= 50, f"only {n_ch}")
r = client.get("/")
check("widget on home", "Challenge of the day" in r.text)
check("archive page", client.get("/challenge").status_code == 200)

from app.challenge import _challenge_for, _grade  # noqa: E402
from datetime import date as _date  # noqa: E402
today_ch = _challenge_for(_date.today())
check("deterministic pick", today_ch["id"] == _challenge_for(_date.today())["id"])

# grading: MC exact, puzzle case/whitespace-insensitive + alternates
mc = db.query_one("SELECT * FROM challenges WHERE kind='quiz' AND question LIKE '%HTTPS%'")
mc = dict(mc) | {"option_list": ["80", "22", "443", "8080"]}
check("MC grade right", _grade(mc, "443") == 1 and _grade(mc, "80") == 0)
pz = dict(db.query_one("SELECT * FROM challenges WHERE answer LIKE 'sha-1|%'")) | {"option_list": []}
check("puzzle alternates", _grade(pz, " SHA1 ") == 1 and _grade(pz, "sha-1") == 1 and _grade(pz, "md5") == 0)

# answer today's challenge (wrong on purpose), verify one-attempt lock
wrong = "definitely-wrong-answer-xyz"
if today_ch["option_list"]:
    wrong = next(o for o in today_ch["option_list"] if o != today_ch["answer"])
r = client.post("/challenge/answer", data={"guess": wrong, "next_url": "/"}, follow_redirects=False)
check("submit answer", r.status_code == 303)
att = db.query_one("SELECT * FROM challenge_attempts")
check("attempt recorded as wrong", att and att["correct"] == 0)
client.post("/challenge/answer", data={"guess": today_ch["answer"].split("|")[0], "next_url": "/"})
check("second attempt blocked",
      db.query_one("SELECT COUNT(*) AS n FROM challenge_attempts")["n"] == 1
      and db.query_one("SELECT correct FROM challenge_attempts")["correct"] == 0)
r = client.get("/")
check("result + explanation shown", ("Not quite" in r.text) and "Answer:" in r.text)

# streak: seed correct answers for yesterday and day before, today wrong = streak from yesterday
import datetime as _dt  # noqa: E402
uid1 = db.query_one("SELECT id FROM users WHERE email='earl@stone-knight.com'")["id"]
for back in (1, 2):
    d = (_date.today() - _dt.timedelta(days=back)).isoformat()
    db.execute("INSERT INTO challenge_attempts (user_id, day, challenge_id, answer, correct) VALUES (?, ?, ?, 'x', 1)",
               (uid1, d, today_ch["id"]))
from app.challenge import _streak  # noqa: E402
check("streak counts back from yesterday", _streak(uid1) == 2)
r = client.get("/challenge")
check("leaderboard shows player", "Earl" in r.text and "🔥 2" in r.text)
check("history shows past days", "Past two weeks" in r.text)

# non-admin lockout
temp_pw = None
client.post("/admin/users", data={"name": "Member", "email": "m@x.com", "role": "member"})
row = db.query_one("SELECT id FROM users WHERE email='m@x.com'")
db.execute("UPDATE users SET password_hash=? WHERE id=?",
           (__import__("app.auth", fromlist=["hash_password"]).hash_password("memberpass1"), row["id"]))
member = TestClient(app)
member.post("/login", data={"email": "m@x.com", "password": "memberpass1"})
check("member blocked from expenses", member.get("/expenses").status_code == 403)
check("member sidebar hides link", "💰 Expenses" not in member.get("/tasks").text)
check("member CAN use CRM", member.get("/crm/leads").status_code == 200)
check("member CAN play challenge", member.get("/challenge").status_code == 200)

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}")
shutil.rmtree(tmp, ignore_errors=True)
raise SystemExit(1 if failures else 0)
