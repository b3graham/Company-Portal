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

# ---------- IT department + asset tracker ----------
r = client.get("/it")
check("IT home renders", r.status_code == 200 and "<h1>IT</h1>" in r.text, r.text[:300])
check("admin sees IT tab", "🖥️ IT" in client.get("/tasks").text)
check("admin can open assets", client.get("/it/assets").status_code == 200)
check("member blocked from IT", member.get("/it/assets").status_code == 403)
check("member sidebar hides IT", "🖥️ IT" not in member.get("/tasks").text)

r = client.post("/it/assets/add",
                data={"tag": "SK-0001", "name": "Field laptop", "type": "laptop", "make": "Dell",
                      "model": "Latitude 5550", "serial": "ABC123XYZ", "purchase_date": "2026-01-15",
                      "cost": "$1,299.00", "vendor": "Dell", "in_service_date": "2026-01-20",
                      "status": "in_service", "assigned_to": uid1, "location": "Office",
                      "warranty_end": "2029-01-15", "notes": "primary machine"},
                follow_redirects=False)
check("add asset", r.status_code == 303, r.text[:300])
r = client.post("/it/assets/add", data={"name": "Old switch", "type": "network", "status": "retired",
                                        "serial": "SW-OLD-1"}, follow_redirects=False)
check("add retired asset", r.status_code == 303, r.text[:300])
check("bad type rejected", client.post("/it/assets/add", data={"name": "x", "type": "toaster"}).status_code == 422)
check("bad cost rejected", client.post("/it/assets/add", data={"name": "x", "cost": "lots"}).status_code == 422)
check("bad date rejected", client.post("/it/assets/add", data={"name": "x", "purchase_date": "1/2/26"}).status_code == 422)

r = client.get("/it/assets")
check("asset listed", "SK-0001" in r.text and "ABC123XYZ" in r.text and "$1,299.00" in r.text)
check("retired hidden by default", "Old switch" not in r.text)
check("all statuses shows retired", "Old switch" in client.get("/it/assets?status=").text)
check("status filter", "Old switch" in client.get("/it/assets?status=retired").text)
check("serial search", "Field laptop" in client.get("/it/assets?q=abc123").text)
check("search misses", "Field laptop" not in client.get("/it/assets?q=nope").text)
check("type filter", "Field laptop" not in client.get("/it/assets?type=server").text)
check("bad filter rejected", client.get("/it/assets?status=bogus").status_code == 422)

aid = db.query_one("SELECT id FROM assets WHERE tag='SK-0001'")["id"]
check("edit form loads", "ABC123XYZ" in client.get(f"/it/assets/{aid}/edit").text)
r = client.post(f"/it/assets/{aid}/edit",
                data={"tag": "SK-0001", "name": "Field laptop", "type": "laptop", "serial": "NEWSERIAL",
                      "status": "repair", "cost": "1299"}, follow_redirects=False)
check("edit asset", r.status_code == 303 and "NEWSERIAL" in client.get("/it/assets").text)
client.post(f"/it/assets/{aid}/status", data={"status": "spare"})
check("quick status change", db.query_one("SELECT status FROM assets WHERE id=?", (aid,))["status"] == "spare")
r = client.get("/it/assets/export/assets.csv?status=")
check("csv export", r.status_code == 200 and "NEWSERIAL" in r.text and "Old switch" in r.text
      and r.headers["content-type"].startswith("text/csv"))
check("member blocked from csv", member.get("/it/assets/export/assets.csv").status_code == 403)
client.post(f"/it/assets/{aid}/delete")
check("delete asset", db.query_one("SELECT 1 FROM assets WHERE id=?", (aid,)) is None)

# put the member in the IT department -> tab appears, access granted
mid = row["id"]
check("bad department rejected", client.post(f"/admin/users/{mid}/department",
                                              data={"department": "sales"}).status_code == 422)
r = client.post(f"/admin/users/{mid}/department", data={"department": "it"}, follow_redirects=False)
check("set department", r.status_code == 303)
check("IT member can open assets", member.get("/it/assets").status_code == 200)
check("IT member sees tab", "🖥️ IT" in member.get("/tasks").text)
check("IT member still blocked from expenses", member.get("/expenses").status_code == 403)
check("users page shows department", 'value="it" selected' in client.get("/admin/users").text)
client.post(f"/admin/users/{mid}/department", data={"department": ""})
check("clear department revokes", member.get("/it/assets").status_code == 403)
r = client.post("/admin/users", data={"name": "Tech", "email": "t@x.com", "role": "member", "department": "it"})
check("create user with department",
      db.query_one("SELECT department FROM users WHERE email='t@x.com'")["department"] == "it")

# ---------- IT: software & licenses ----------
check("licenses page", client.get("/it/licenses").status_code == 200)
r = client.post("/it/licenses/add",
                data={"name": "Office Suite", "vendor": "BigCorp", "category": "productivity",
                      "seats": "5", "cost": "$12.50", "billing": "monthly",
                      "renewal_date": (_date.today() + _dt.timedelta(days=10)).isoformat(),
                      "auto_renew": "1", "owner_id": uid1, "login_url": "https://example.com",
                      "account": "admin@example.com"}, follow_redirects=False)
check("add license", r.status_code == 303, r.text[:300])
r = client.post("/it/licenses/add", data={"name": "Old Scanner", "category": "security",
                                          "cost": "500", "billing": "annual", "status": "cancelled"},
                follow_redirects=False)
check("add cancelled license", r.status_code == 303)
check("bad seats rejected", client.post("/it/licenses/add", data={"name": "x", "seats": "five"}).status_code == 422)
check("bad billing rejected", client.post("/it/licenses/add", data={"name": "x", "billing": "weekly"}).status_code == 422)
r = client.get("/it/licenses")
check("license listed", "Office Suite" in r.text and "$12.50" in r.text and "(soon)" in r.text)
check("cancelled hidden by default", "Old Scanner" not in r.text)
check("annualized spend = $150.00", "$150.00" in r.text, r.text[:100])
check("all statuses shows cancelled", "Old Scanner" in client.get("/it/licenses?status=").text)
check("license search", "Office Suite" in client.get("/it/licenses?q=bigcorp").text)
check("license category filter", "Office Suite" not in client.get("/it/licenses?category=security").text)
lid_ = db.query_one("SELECT id FROM licenses WHERE name='Office Suite'")["id"]
check("license edit form", "BigCorp" in client.get(f"/it/licenses/{lid_}/edit").text)
r = client.post(f"/it/licenses/{lid_}/edit",
                data={"name": "Office Suite Pro", "category": "productivity", "billing": "annual",
                      "cost": "150", "status": "active"}, follow_redirects=False)
check("edit license", r.status_code == 303 and "Office Suite Pro" in client.get("/it/licenses").text)
r = client.get("/it/licenses/export/licenses.csv?status=")
check("licenses csv", r.status_code == 200 and "Office Suite Pro" in r.text and "Old Scanner" in r.text)
check("member blocked from licenses", member.get("/it/licenses").status_code == 403)
client.post(f"/it/licenses/{lid_}/delete")
check("delete license", db.query_one("SELECT 1 FROM licenses WHERE id=?", (lid_,)) is None)

# ---------- IT: vendors ----------
check("vendors page", client.get("/it/vendors").status_code == 200)
r = client.post("/it/vendors/add",
                data={"name": "Example ISP", "category": "isp", "account_no": "ACCT-42",
                      "contact_name": "Rep", "support_phone": "555 0100",
                      "support_email": "help@example.com", "portal_url": "https://example.com",
                      "contract_end": "2027-01-01", "notes": "24/7 line"}, follow_redirects=False)
check("add vendor", r.status_code == 303, r.text[:300])
check("bad vendor category", client.post("/it/vendors/add", data={"name": "x", "category": "zzz"}).status_code == 422)
r = client.get("/it/vendors")
check("vendor listed", "Example ISP" in r.text and "ACCT-42" in r.text and "tel:5550100" in r.text)
check("vendor search", "Example ISP" in client.get("/it/vendors?q=acct").text)
check("vendor category filter", "Example ISP" not in client.get("/it/vendors?category=hosting").text)
vid = db.query_one("SELECT id FROM vendors WHERE name='Example ISP'")["id"]
r = client.post(f"/it/vendors/{vid}/edit", data={"name": "Example ISP Inc", "category": "isp"},
                follow_redirects=False)
check("edit vendor", r.status_code == 303 and "Example ISP Inc" in client.get("/it/vendors").text)
check("member blocked from vendors", member.get("/it/vendors").status_code == 403)
client.post(f"/it/vendors/{vid}/delete")
check("delete vendor", db.query_one("SELECT 1 FROM vendors WHERE id=?", (vid,)) is None)

# ---------- IT: document engine ----------
for sec in ("policies", "runbooks", "dr"):
    check(f"docs section {sec}", client.get(f"/it/docs/{sec}").status_code == 200)
check("unknown section 404", client.get("/it/docs/nope").status_code == 404)
r = client.get("/it/docs/policies")
check("starter policies seeded", "Acceptable Use Policy" in r.text and "Password and MFA Standard" in r.text)
check("starter runbooks seeded", "VPN Setup" in client.get("/it/docs/runbooks").text)
check("starter DR seeded", "Disaster Recovery Plan" in client.get("/it/docs/dr").text)
check("seeded docs are drafts", db.query_one("SELECT COUNT(*) AS n FROM it_docs WHERE status != 'draft'")["n"] == 0)

r = client.post("/it/docs/runbooks/new",
                data={"title": "Printer reset", "content": "# Printer reset\n\n1. Unplug it.",
                      "status": "active", "owner_id": uid1, "review_date": "2020-01-01"},
                follow_redirects=False)
check("create doc", r.status_code == 303 and r.headers["location"].startswith("/it/doc/"))
did = int(r.headers["location"].rsplit("/", 1)[1])
r = client.get(f"/it/doc/{did}")
check("view doc renders markdown", "<h1>Printer reset</h1>" in r.text and "Unplug it" in r.text)
check("doc listed in section", "Printer reset" in client.get("/it/docs/runbooks").text)
check("doc search", "Printer reset" in client.get("/it/docs/runbooks?q=unplug").text)
check("doc search misses", "Printer reset" not in client.get("/it/docs/runbooks?q=zzzz").text)
check("overdue review on IT home", "Printer reset" in client.get("/it").text)
check("bad doc status", client.post(f"/it/doc/{did}/edit", data={"title": "x", "status": "live"}).status_code == 422)

r = client.post(f"/it/doc/{did}/edit",
                data={"title": "Printer reset", "content": "# Printer reset\n\n1. Unplug it.\n2. Wait 30s.",
                      "status": "active", "owner_id": uid1, "review_date": "2020-01-01"},
                follow_redirects=False)
check("edit doc", r.status_code == 303)
check("two versions", db.query_one("SELECT COUNT(*) AS n FROM it_doc_versions WHERE doc_id=?", (did,))["n"] == 2)
r = client.post(f"/it/doc/{did}/edit",
                data={"title": "Printer reset", "content": "# Printer reset\n\n1. Unplug it.\n2. Wait 30s.",
                      "status": "active", "owner_id": uid1, "review_date": "2030-01-01"},
                follow_redirects=False)
check("metadata-only edit adds no version",
      db.query_one("SELECT COUNT(*) AS n FROM it_doc_versions WHERE doc_id=?", (did,))["n"] == 2)
check("review no longer overdue", "Printer reset" not in client.get("/it").text)
first = db.query_one("SELECT id FROM it_doc_versions WHERE doc_id=? ORDER BY id", (did,))["id"]
check("version view", "Unplug it" in client.get(f"/it/doc/{did}/versions/{first}").text)
r = client.post(f"/it/doc/{did}/versions/{first}/restore", follow_redirects=False)
check("restore version", r.status_code == 303
      and "Wait 30s" not in db.query_one("SELECT content FROM it_docs WHERE id=?", (did,))["content"]
      and db.query_one("SELECT COUNT(*) AS n FROM it_doc_versions WHERE doc_id=?", (did,))["n"] == 3)
r = client.post(f"/it/doc/{did}/reviewed", data={"months": "6"}, follow_redirects=False)
check("mark reviewed", r.status_code == 303
      and db.query_one("SELECT review_date FROM it_docs WHERE id=?", (did,))["review_date"] > _date.today().isoformat())
check("bad review interval", client.post(f"/it/doc/{did}/reviewed", data={"months": "5"}).status_code == 422)
client.post(f"/it/doc/{did}/status", data={"status": "retired"})
check("retired hidden by default", "Printer reset" not in client.get("/it/docs/runbooks").text)
check("retired shown with show=all", "Printer reset" in client.get("/it/docs/runbooks?show=all").text)
check("member blocked from docs", member.get("/it/docs/policies").status_code == 403
      and member.get(f"/it/doc/{did}").status_code == 403)

# attachments on IT docs
r = client.post(f"/it/doc/{did}/files", files={"upload": ("signed policy.pdf", b"%PDF-1.4 fake", "application/pdf")},
                follow_redirects=False)
check("attach file", r.status_code == 303 and r.headers["location"].startswith(f"/it/doc/{did}"))
att = db.query_one("SELECT * FROM it_doc_files WHERE doc_id=?", (did,))
check("attachment recorded", att is not None and att["orig_name"] == "signed policy.pdf"
      and att["stored_name"].endswith(".pdf") and att["size"] == 13)
att_path = os.path.join(db.UPLOAD_DIR, att["stored_name"])
check("attachment on disk", os.path.isfile(att_path))
check("attachment listed on doc", "signed policy.pdf" in client.get(f"/it/doc/{did}?show=all").text)
r = client.get(f"/it/doc/{did}/files/{att['id']}")
check("attachment download", r.status_code == 200 and r.content == b"%PDF-1.4 fake"
      and "signed" in r.headers.get("content-disposition", ""))
check("attachment 404 on other doc", client.get(f"/it/doc/{did + 999}/files/{att['id']}").status_code == 404)
check("member blocked from attachment", member.get(f"/it/doc/{did}/files/{att['id']}").status_code == 403)
r = client.post(f"/it/doc/{did}/files/{att['id']}/delete", follow_redirects=False)
check("remove attachment", r.status_code == 303 and not os.path.isfile(att_path)
      and db.query_one("SELECT 1 FROM it_doc_files WHERE id=?", (att["id"],)) is None)
client.post(f"/it/doc/{did}/files", files={"upload": ("diagram.png", png_bytes(), "image/png")})
att2 = db.query_one("SELECT stored_name FROM it_doc_files WHERE doc_id=?", (did,))
att2_path = os.path.join(db.UPLOAD_DIR, att2["stored_name"])
client.post(f"/it/doc/{did}/delete")
check("delete doc removes attachments",
      db.query_one("SELECT 1 FROM it_doc_files WHERE doc_id=?", (did,)) is None and not os.path.isfile(att2_path))
check("delete doc cascades versions",
      db.query_one("SELECT 1 FROM it_docs WHERE id=?", (did,)) is None
      and db.query_one("SELECT COUNT(*) AS n FROM it_doc_versions WHERE doc_id=?", (did,))["n"] == 0)
check("home counts drafts", "Docs still in draft" in client.get("/it").text)

# ---------- IT: accounts & access ----------
check("IR plan seeded", "Incident Response Plan" in client.get("/it/docs/ir").text)
check("access page", client.get("/it/access").status_code == 200)
r = client.post("/it/access/add", data={"user_id": uid1, "system": "Email", "level": "admin",
                                        "mfa": "1", "granted": "2026-01-01"}, follow_redirects=False)
check("add grant (user)", r.status_code == 303, r.text[:300])
r = client.post("/it/access/add", data={"person": "Outside Contractor", "system": "VPN",
                                        "level": "user"}, follow_redirects=False)
check("add grant (external, no mfa)", r.status_code == 303, r.text[:300])
check("grant needs a who", client.post("/it/access/add", data={"system": "x"}).status_code == 422)
check("bad level", client.post("/it/access/add", data={"user_id": uid1, "system": "x", "level": "root"}).status_code == 422)
r = client.get("/it/access")
check("grants grouped by person", "Outside Contractor" in r.text and "Email" in r.text and "✗ no" in r.text)
check("person filter", "<h2>Earl" not in client.get("/it/access?person=Outside+Contractor").text)
check("system filter", "<h2>Outside Contractor" not in client.get("/it/access?system=Email").text)
gid = db.query_one("SELECT id FROM access_grants WHERE system='VPN'")["id"]
r = client.post(f"/it/access/{gid}/edit", data={"person": "Outside Contractor", "system": "VPN",
                                                "level": "readonly", "mfa": "1", "last_review": "2020-01-01"},
                follow_redirects=False)
check("edit grant", r.status_code == 303 and "Read-only" in client.get("/it/access").text)
check("stale review flagged", "2020-01-01" in client.get("/it/access").text)
client.post(f"/it/access/{gid}/reviewed")
check("mark grant reviewed", db.query_one("SELECT last_review FROM access_grants WHERE id=?", (gid,))["last_review"] == _date.today().isoformat())
r = client.post("/it/access-review", data={"user_id": uid1}, follow_redirects=False)
check("person reviewed", r.status_code == 303)
client.post(f"/it/access/{gid}/revoke")
check("revoke hides by default", "<strong>VPN</strong>" not in client.get("/it/access").text
      and "<strong>VPN</strong>" in client.get("/it/access?show=all").text)
r = client.post("/it/access-offboard", data={"user_id": uid1}, follow_redirects=False)
check("offboard revokes all", r.status_code == 303
      and db.query_one("SELECT COUNT(*) AS n FROM access_grants WHERE user_id=? AND revoked=''", (uid1,))["n"] == 0)
r = client.get("/it/access/export/access.csv?show=all")
check("access csv", r.status_code == 200 and "Outside Contractor" in r.text)
check("member blocked from access", member.get("/it/access").status_code == 403)
client.post(f"/it/access/{gid}/delete")
check("delete grant", db.query_one("SELECT 1 FROM access_grants WHERE id=?", (gid,)) is None)

# ---------- IT: backups ----------
check("backups page", client.get("/it/backups").status_code == 200)
r = client.post("/it/backups/add", data={"system": "Portal data", "method": "tar", "frequency": "Nightly",
                                         "retention": "30 days", "location": "VPS", "offsite": "1",
                                         "owner_id": uid1}, follow_redirects=False)
check("add backup", r.status_code == 303, r.text[:300])
r = client.get("/it/backups")
check("backup listed and overdue", "Portal data" in r.text and '<div class="small">test overdue</div>' in r.text)
bid = db.query_one("SELECT id FROM backups WHERE system='Portal data'")["id"]
r = client.post(f"/it/backups/{bid}/test", data={"result": "pass", "notes": "restored to temp"}, follow_redirects=False)
check("log restore test", r.status_code == 303)
check("bad result", client.post(f"/it/backups/{bid}/test", data={"result": "meh"}).status_code == 422)
r = client.get("/it/backups")
check("no longer overdue", '<div class="small">test overdue</div>' not in r.text and "· Pass" in r.text)
client.post(f"/it/backups/{bid}/test", data={"result": "fail", "notes": "checksum mismatch"})
check("failed test flagged", "Fail" in client.get("/it/backups").text)
r = client.get(f"/it/backups/{bid}/edit")
check("history shown", "restored to temp" in r.text and "checksum mismatch" in r.text)
tid_ = db.query_one("SELECT id FROM backup_tests WHERE result='fail'")["id"]
client.post(f"/it/backups/tests/{tid_}/delete")
check("delete test", db.query_one("SELECT 1 FROM backup_tests WHERE id=?", (tid_,)) is None)
r = client.post(f"/it/backups/{bid}/edit", data={"system": "Portal data", "active": ""}, follow_redirects=False)
check("retire backup", r.status_code == 303 and "Portal data" not in client.get("/it/backups").text
      and "Portal data" in client.get("/it/backups?show=all").text)
check("member blocked from backups", member.get("/it/backups").status_code == 403)
client.post(f"/it/backups/{bid}/delete")
check("delete backup cascades", db.query_one("SELECT COUNT(*) AS n FROM backup_tests WHERE backup_id=?", (bid,))["n"] == 0)

# ---------- IT: incidents ----------
check("incidents page", client.get("/it/incidents").status_code == 200)
check("IR plan linked", "Incident Response Plan" in client.get("/it/incidents").text)
r = client.post("/it/incidents/add", data={"title": "Phish click", "severity": "high",
                                           "lead_id": uid1, "systems": "Mailbox",
                                           "summary": "User clicked a link"}, follow_redirects=False)
check("open incident", r.status_code == 303 and r.headers["location"].startswith("/it/incidents/"))
iid = int(r.headers["location"].rsplit("/", 1)[1])
check("bad severity", client.post("/it/incidents/add", data={"title": "x", "severity": "meh"}).status_code == 422)
r = client.get(f"/it/incidents/{iid}")
check("incident view", "Phish click" in r.text and "Incident opened by" in r.text)
check("open incident on IT home", "Phish click" in client.get("/it").text)
client.post(f"/it/incidents/{iid}/note", data={"note": "Reset the password"})
check("timeline note appended", "Reset the password" in client.get(f"/it/incidents/{iid}").text)
client.post(f"/it/incidents/{iid}/status", data={"status": "contained"})
check("status change logged", "Status set to Contained" in db.query_one("SELECT timeline FROM incidents WHERE id=?", (iid,))["timeline"])
r = client.post(f"/it/incidents/{iid}/edit",
                data={"title": "Phish click", "severity": "high", "status": "closed", "detected_on": "2026-09-01",
                      "systems": "Mailbox", "summary": "s", "timeline": "- done", "lessons": "Train"},
                follow_redirects=False)
check("edit closes with date", r.status_code == 303
      and db.query_one("SELECT closed_on FROM incidents WHERE id=?", (iid,))["closed_on"] == _date.today().isoformat())
check("closed hidden by default", "Phish click" not in client.get("/it/incidents").text
      and "Phish click" in client.get("/it/incidents?status=closed").text)
check("incident search", "Phish click" in client.get("/it/incidents?status=&q=mailbox").text)
r = client.get("/it/incidents/export/incidents.csv")
check("incidents csv", r.status_code == 200 and "Phish click" in r.text)
check("member blocked from incidents", member.get("/it/incidents").status_code == 403
      and member.get(f"/it/incidents/{iid}").status_code == 403)
client.post(f"/it/incidents/{iid}/delete")
check("delete incident", db.query_one("SELECT 1 FROM incidents WHERE id=?", (iid,)) is None)

# Everlance import
import re as _re
_trips = ('==========,==========\nMade with Everlance.\nExport Type, Trips\n""\n'
          '"Value","Miles","From","To","Date","Purpose","Tags","Business Line","Vehicle","Time Started","Time Ended","Notes"\n'
          '$2.21,3.3,"Home Office","Camp Blanding",2026-02-10,"Work","","Guardsman","","08:39 PM","08:50 PM",""\n'
          '$0.40,0.6,"Home Office","Kroger",2026-02-11,"Personal","","","","","",""\n'
          '$1.00,1.5,"Home Office","Post Office",2026-02-12,"","","","","","",""\n')
r = client.post("/expenses/import/preview", files={"upload": ("trips.csv", _trips.encode(), "text/csv")})
check("import preview", r.status_code == 200 and "Guardsman" in r.text, r.text[:300])
tok = _re.search(r'name="token" value="([0-9a-f]+)"', r.text).group(1)
nat = db.query_one("SELECT id FROM expense_lists WHERE name='National Guard'")["id"]
r = client.post("/expenses/import/commit", data={"token": tok, "purpose_mode": "work",
                "entity__": ent, "entity__Guardsman": nat, "vehicle__": ""}, follow_redirects=False)
check("import work trips only", "Imported+1+trips" in r.headers.get("location", ""), r.headers.get("location"))
trip = db.query_one("SELECT * FROM mileage_trips WHERE to_place='Camp Blanding'")
check("imported trip mapped", trip and trip["entity_id"] == nat and "08:39 PM" in trip["notes"])
r = client.post("/expenses/import/preview", files={"upload": ("trips.csv", _trips.encode(), "text/csv")})
tok = _re.search(r'name="token" value="([0-9a-f]+)"', r.text).group(1)
r = client.post("/expenses/import/commit", data={"token": tok, "purpose_mode": "work",
                "entity__": ent, "entity__Guardsman": nat, "vehicle__": ""}, follow_redirects=False)
check("import dedupes rerun", "Imported+0+trips" in r.headers.get("location", ""), r.headers.get("location"))
batch = db.query_one("SELECT id FROM import_batches WHERE inserted = 1")["id"]
client.post(f"/expenses/import/{batch}/undo")
check("import undo", db.query_one("SELECT 1 FROM mileage_trips WHERE to_place='Camp Blanding'") is None)
check("bad import rejected",
      client.post("/expenses/import/preview", files={"upload": ("x.csv", b"a,b\n1,2\n", "text/csv")}).status_code == 422)
check("member blocked from import", member.get("/expenses/import").status_code == 403)

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}")
shutil.rmtree(tmp, ignore_errors=True)
raise SystemExit(1 if failures else 0)
