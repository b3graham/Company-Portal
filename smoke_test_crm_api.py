"""Smoke test for the read-only CRM API. Run:  python smoke_test_crm_api.py"""
import os
import tempfile
from datetime import date, timedelta

tmp = tempfile.mkdtemp(prefix="portal-test-")
os.environ["DATA_DIR"] = tmp
os.environ.pop("CRM_API_TOKEN", None)

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


TOKEN = "test-token-abc123"
H = {"Authorization": f"Bearer {TOKEN}"}

# --- unconfigured: API is invisible even with a token ---
r = client.get("/api/crm/summary", headers=H)
check("unset token -> 404", r.status_code == 404, r.text[:200])

# --- configure and seed through the normal UI as the admin ---
os.environ["CRM_API_TOKEN"] = TOKEN
r = client.post("/setup", data={"name": "Earl", "email": "earl@stone-knight.com",
                                "password": "testpass123"}, follow_redirects=False)
check("setup", r.status_code == 303, r.text[:200])
yesterday = (date.today() - timedelta(days=1)).isoformat()
r = client.post("/crm/contacts/add", data={"name": "Ada Lovelace", "company": "Analytical Co",
                                            "email": "ada@example.com"}, follow_redirects=False)
check("add contact", r.status_code == 303, r.text[:200])
cid = db.query_one("SELECT id FROM contacts WHERE name='Ada Lovelace'")["id"]
r = client.post("/crm/leads/add", data={"title": "Engine audit", "contact_id": str(cid),
                                         "source": "referral", "follow_up": yesterday},
                follow_redirects=False)
check("add lead", r.status_code == 303, r.text[:200])
lid = db.query_one("SELECT id FROM leads WHERE title='Engine audit'")["id"]
r = client.post("/crm/projects/add", data={"name": "Audit kickoff", "contact_id": str(cid),
                                            "due_date": yesterday}, follow_redirects=False)
check("add project", r.status_code == 303, r.text[:200])
pid = db.query_one("SELECT id FROM projects WHERE name='Audit kickoff'")["id"]

# --- auth: session cookie alone is not enough; token alone is ---
r = client.get("/api/crm/summary")
check("cookie only -> 401", r.status_code == 401 and r.headers.get("www-authenticate") == "Bearer")
r = client.get("/api/crm/summary", headers={"Authorization": "Bearer wrong"})
check("wrong token -> 401", r.status_code == 401)
r = client.get("/api/crm/summary", headers={"Authorization": f"Basic {TOKEN}"})
check("wrong scheme -> 401", r.status_code == 401)
anon = TestClient(app)  # no session cookie
r = anon.get("/api/crm/summary", headers=H)
check("token, no cookie -> 200", r.status_code == 200, r.text[:200])
s = r.json()
check("summary counts", s["contacts"] == 1 and s["leads_by_stage"]["new"] == 1
      and s["projects_by_status"]["planned"] == 1, str(s)[:300])
check("summary follow-up due", [x["id"] for x in s["follow_ups_due"]] == [lid])
check("summary project due", [x["id"] for x in s["projects_due"]] == [pid])

# --- token grants nothing on the HTML app ---
r = anon.get("/crm/leads", headers=H, follow_redirects=False)
check("token on /crm/leads -> login redirect", r.status_code == 303 and "/login" in r.headers["location"])
r = anon.post("/crm/contacts/add", headers=H, data={"name": "Mallory"}, follow_redirects=False)
check("token on POST /crm -> no write", r.status_code in (303, 401, 403)
      and not db.query_one("SELECT 1 FROM contacts WHERE name='Mallory'"))

# --- no write verbs on the API ---
for method in ("post", "put", "patch", "delete"):
    r = getattr(anon, method)("/api/crm/contacts", headers=H)
    check(f"{method.upper()} /api/crm/contacts -> 405", r.status_code == 405, str(r.status_code))
r = anon.post(f"/api/crm/contacts/{cid}", headers=H, data={"name": "x"})
check("POST /api/crm/contacts/{id} -> 405", r.status_code == 405, str(r.status_code))

# --- lists, filters, detail ---
r = anon.get("/api/crm/contacts", headers=H, params={"q": "analytical"})
check("contacts q", r.status_code == 200 and [c["id"] for c in r.json()["items"]] == [cid]
      and r.json()["items"][0]["n_leads"] == 1, r.text[:300])
r = anon.get("/api/crm/contacts", headers=H, params={"q": "nobody"})
check("contacts q miss", r.json()["items"] == [])
r = anon.get(f"/api/crm/contacts/{cid}", headers=H)
j = r.json()
check("contact detail", j["name"] == "Ada Lovelace" and [x["id"] for x in j["leads"]] == [lid]
      and [x["id"] for x in j["projects"]] == [pid], r.text[:300])
check("contact 404", anon.get("/api/crm/contacts/9999", headers=H).status_code == 404)

r = anon.get("/api/crm/leads", headers=H, params={"open": "true"})
check("leads open", [x["id"] for x in r.json()["items"]] == [lid]
      and r.json()["items"][0]["contact_name"] == "Ada Lovelace", r.text[:300])
check("leads stage=won empty", anon.get("/api/crm/leads", headers=H,
                                        params={"stage": "won"}).json()["items"] == [])
check("leads bad stage 422", anon.get("/api/crm/leads", headers=H,
                                      params={"stage": "bogus"}).status_code == 422)
check("leads due_by today", [x["id"] for x in anon.get(
    "/api/crm/leads", headers=H, params={"due_by": date.today().isoformat()}).json()["items"]] == [lid])
check("leads bad date 422", anon.get("/api/crm/leads", headers=H,
                                     params={"due_by": "11/04/2026"}).status_code == 422)
r = anon.get(f"/api/crm/leads/{lid}", headers=H)
check("lead detail", r.json()["title"] == "Engine audit" and r.json()["created_by_name"] == "Earl", r.text[:300])

r = anon.get("/api/crm/projects", headers=H, params={"status": "planned"})
check("projects status", [x["id"] for x in r.json()["items"]] == [pid], r.text[:300])
check("projects bad status 422", anon.get("/api/crm/projects", headers=H,
                                          params={"status": "bogus"}).status_code == 422)
r = anon.get(f"/api/crm/projects/{pid}", headers=H)
check("project detail", r.json()["name"] == "Audit kickoff" and r.json()["contact_company"] == "Analytical Co")
check("project 404", anon.get("/api/crm/projects/9999", headers=H).status_code == 404)

# --- pagination ---
check("limit 0 -> 422", anon.get("/api/crm/contacts", headers=H, params={"limit": 0}).status_code == 422)
check("limit 501 -> 422", anon.get("/api/crm/contacts", headers=H, params={"limit": 501}).status_code == 422)
check("offset past end", anon.get("/api/crm/contacts", headers=H,
                                  params={"offset": 5}).json()["items"] == [])

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
raise SystemExit(1 if failures else 0)
