"""IT operations tools (second pass): accounts & access register, backup inventory
with restore-test log, and the incident log. IT department only (and admins)."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db
from .it import _check_enum, _csv, _opt_date, _opt_user, _people, require_it
from .web import page

router = APIRouter()

ACCESS_LEVELS = ["admin", "user", "readonly"]
LEVEL_LABELS = {"admin": "Admin", "user": "User", "readonly": "Read-only"}
ACCESS_REVIEW_DAYS = 90

BACKUP_TEST_DAYS = 180
TEST_RESULTS = ["pass", "partial", "fail"]
RESULT_LABELS = {"pass": "Pass", "partial": "Partial", "fail": "Fail"}

SEVERITIES = ["low", "medium", "high", "critical"]
SEVERITY_LABELS = {"low": "Low", "medium": "Medium", "high": "High", "critical": "Critical"}
INCIDENT_STATUSES = ["open", "contained", "resolved", "closed"]
INCIDENT_STATUS_LABELS = {"open": "Open", "contained": "Contained",
                          "resolved": "Resolved", "closed": "Closed"}


def _today():
    return date.today().isoformat()


def _days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


# ======================================================================
# Accounts & access
# ======================================================================

def _grant_rows(where="1=1", args=()):
    return db.query(
        f"""SELECT g.*, COALESCE(u.name, g.person) AS who, u.active AS user_active
            FROM access_grants g LEFT JOIN users u ON u.id = g.user_id
            WHERE {where}
            ORDER BY (g.revoked != ''), who COLLATE NOCASE, g.system COLLATE NOCASE""", args)


def _grant_filters(q, system, person, show):
    where, args = [], []
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        where.append("(g.system LIKE ? OR g.person LIKE ? OR u.name LIKE ? OR g.notes LIKE ?)")
        args += [like] * 4
    if system:
        where.append("g.system = ?")
        args.append(system)
    if person:
        where.append("COALESCE(u.name, g.person) = ?")
        args.append(person)
    if show != "all":
        where.append("g.revoked = ''")
    return " AND ".join(where) or "1=1", tuple(args)


def _get_grant(grant_id):
    row = db.query_one("SELECT * FROM access_grants WHERE id = ?", (grant_id,))
    if not row:
        raise HTTPException(404, "grant not found")
    return row


def _who(user_id_raw, person_raw):
    """A grant belongs to a portal user or to a named outsider (contractor, vendor)."""
    uid = _opt_user(user_id_raw)
    person = (person_raw or "").strip()
    if uid is None and not person:
        raise HTTPException(422, "pick a user or type a name")
    return uid, "" if uid else person


def _access_ctx():
    systems = [r["system"] for r in db.query(
        "SELECT DISTINCT system FROM access_grants ORDER BY system COLLATE NOCASE")]
    people = [r["who"] for r in db.query(
        """SELECT DISTINCT COALESCE(u.name, g.person) AS who FROM access_grants g
           LEFT JOIN users u ON u.id = g.user_id ORDER BY who COLLATE NOCASE""")]
    return dict(levels=ACCESS_LEVELS, level_labels=LEVEL_LABELS, users=_people(),
                systems=systems, persons=people, today=_today(),
                review_days=ACCESS_REVIEW_DAYS, stale_before=_days_ago(ACCESS_REVIEW_DAYS))


@router.get("/it/access")
def access_list(request: Request, q: str = "", system: str = "", person: str = "",
                show: str = "active", user=Depends(require_it)):
    where, args = _grant_filters(q, system, person, show)
    rows = _grant_rows(where, args)
    groups = []
    for r in rows:
        if not groups or groups[-1]["who"] != r["who"]:
            groups.append({"who": r["who"], "user_id": r["user_id"], "person": r["person"],
                           "user_active": r["user_active"], "rows": []})
        groups[-1]["rows"].append(r)
    stale = _days_ago(ACCESS_REVIEW_DAYS)
    stats = db.query_one(
        """SELECT COUNT(*) AS active,
                  COUNT(DISTINCT COALESCE(u.name, g.person)) AS people,
                  COUNT(DISTINCT g.system) AS systems,
                  SUM(CASE WHEN g.mfa = 0 THEN 1 ELSE 0 END) AS no_mfa,
                  SUM(CASE WHEN g.last_review = '' OR g.last_review < ? THEN 1 ELSE 0 END) AS unreviewed,
                  SUM(CASE WHEN u.id IS NOT NULL AND u.active = 0 THEN 1 ELSE 0 END) AS orphaned
           FROM access_grants g LEFT JOIN users u ON u.id = g.user_id WHERE g.revoked = ''""",
        (stale,))
    qs = "&".join(f"{k}={v}" for k, v in (("q", q), ("system", system), ("person", person),
                                          ("show", show)) if v)
    return page(request, "it_access.html", user=user, tab="access", groups=groups, n=len(rows),
                q=q, system=system, person=person, show=show, qs=("?" + qs) if qs else "",
                stats=stats, **_access_ctx())


@router.post("/it/access/add")
def access_add(user=Depends(require_it),
               user_id: str = Form(""), person: str = Form(""), system: str = Form(...),
               level: str = Form("user"), mfa: str = Form(""), granted: str = Form(""),
               notes: str = Form("")):
    uid, who = _who(user_id, person)
    db.execute(
        """INSERT INTO access_grants (user_id, person, system, level, mfa, granted, last_review, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (uid, who, system.strip(), _check_enum(level, ACCESS_LEVELS, "level"), 1 if mfa else 0,
         _opt_date(granted) or _today(), _today(), notes.strip()))
    return RedirectResponse("/it/access?msg=Access+recorded", status_code=303)


@router.get("/it/access/{grant_id}/edit")
def access_edit_form(request: Request, grant_id: int, user=Depends(require_it)):
    return page(request, "access_edit.html", user=user, tab="access", grant=_get_grant(grant_id),
                **_access_ctx())


@router.post("/it/access/{grant_id}/edit")
def access_edit(grant_id: int, user=Depends(require_it),
                user_id: str = Form(""), person: str = Form(""), system: str = Form(...),
                level: str = Form("user"), mfa: str = Form(""), granted: str = Form(""),
                last_review: str = Form(""), revoked: str = Form(""), notes: str = Form("")):
    _get_grant(grant_id)
    uid, who = _who(user_id, person)
    db.execute(
        """UPDATE access_grants SET user_id = ?, person = ?, system = ?, level = ?, mfa = ?,
           granted = ?, last_review = ?, revoked = ?, notes = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (uid, who, system.strip(), _check_enum(level, ACCESS_LEVELS, "level"), 1 if mfa else 0,
         _opt_date(granted), _opt_date(last_review), _opt_date(revoked), notes.strip(), grant_id))
    return RedirectResponse("/it/access?msg=Access+saved", status_code=303)


@router.post("/it/access/{grant_id}/revoke")
def access_revoke(grant_id: int, user=Depends(require_it)):
    _get_grant(grant_id)
    db.execute("UPDATE access_grants SET revoked = ?, updated_at = datetime('now') WHERE id = ?",
               (_today(), grant_id))
    return RedirectResponse("/it/access?msg=Access+revoked", status_code=303)


@router.post("/it/access/{grant_id}/reviewed")
def access_reviewed(grant_id: int, user=Depends(require_it)):
    _get_grant(grant_id)
    db.execute("UPDATE access_grants SET last_review = ?, updated_at = datetime('now') WHERE id = ?",
               (_today(), grant_id))
    return RedirectResponse("/it/access", status_code=303)


@router.post("/it/access/{grant_id}/delete")
def access_delete(grant_id: int, user=Depends(require_it)):
    _get_grant(grant_id)
    db.execute("DELETE FROM access_grants WHERE id = ?", (grant_id,))
    return RedirectResponse("/it/access?msg=Entry+deleted", status_code=303)


def _person_where(user_id_raw, person):
    uid = _opt_user(user_id_raw)
    if uid is not None:
        return "user_id = ?", (uid,)
    person = (person or "").strip()
    if not person:
        raise HTTPException(422, "pick a person")
    return "user_id IS NULL AND person = ?", (person,)


@router.post("/it/access-review")
def access_person_reviewed(user_id: str = Form(""), person: str = Form(""), user=Depends(require_it)):
    """Quarterly review: mark every active grant for one person as reviewed today."""
    where, args = _person_where(user_id, person)
    db.execute(f"UPDATE access_grants SET last_review = ?, updated_at = datetime('now') WHERE {where} AND revoked = ''",
               (_today(),) + args)
    return RedirectResponse("/it/access?msg=Marked+reviewed", status_code=303)


@router.post("/it/access-offboard")
def access_person_revoke(user_id: str = Form(""), person: str = Form(""), user=Depends(require_it)):
    """Offboarding: revoke every active grant for one person in one go."""
    where, args = _person_where(user_id, person)
    db.execute(f"UPDATE access_grants SET revoked = ?, updated_at = datetime('now') WHERE {where} AND revoked = ''",
               (_today(),) + args)
    return RedirectResponse("/it/access?show=all&msg=All+access+revoked", status_code=303)


@router.get("/it/access/export/access.csv")
def access_export(q: str = "", system: str = "", person: str = "", show: str = "active",
                  user=Depends(require_it)):
    where, args = _grant_filters(q, system, person, show)
    return _csv("access.csv",
                ["Person", "System", "Level", "MFA", "Granted", "Last review", "Revoked", "Notes"],
                [[g["who"], g["system"], LEVEL_LABELS[g["level"]], "yes" if g["mfa"] else "no",
                  g["granted"], g["last_review"], g["revoked"], g["notes"]]
                 for g in _grant_rows(where, args)])


# ======================================================================
# Backups & restore tests
# ======================================================================

def _backup_rows(where="1=1", args=()):
    return db.query(
        f"""SELECT b.*, u.name AS owner,
                   (SELECT tested_on FROM backup_tests t WHERE t.backup_id = b.id
                    ORDER BY tested_on DESC, id DESC LIMIT 1) AS last_test,
                   (SELECT result FROM backup_tests t WHERE t.backup_id = b.id
                    ORDER BY tested_on DESC, id DESC LIMIT 1) AS last_result
            FROM backups b LEFT JOIN users u ON u.id = b.owner_id
            WHERE {where} ORDER BY b.active DESC, b.system COLLATE NOCASE""", args)


def _get_backup(backup_id):
    row = db.query_one("SELECT * FROM backups WHERE id = ?", (backup_id,))
    if not row:
        raise HTTPException(404, "backup not found")
    return row


def _backup_ctx():
    return dict(results=TEST_RESULTS, result_labels=RESULT_LABELS, people=_people(),
                today=_today(), due_before=_days_ago(BACKUP_TEST_DAYS), test_days=BACKUP_TEST_DAYS)


@router.get("/it/backups")
def backups_list(request: Request, show: str = "active", user=Depends(require_it)):
    rows = _backup_rows("1=1" if show == "all" else "b.active = 1")
    due_before = _days_ago(BACKUP_TEST_DAYS)
    active = [r for r in rows if r["active"]]
    n_due = sum(1 for r in active if not r["last_test"] or r["last_test"] < due_before)
    n_failed = sum(1 for r in active if r["last_result"] in ("fail", "partial"))
    n_offsite = sum(1 for r in active if r["offsite"])
    return page(request, "it_backups.html", user=user, tab="backups", rows=rows, show=show,
                n_due=n_due, n_failed=n_failed, n_offsite=n_offsite, n_active=len(active),
                **_backup_ctx())


def _backup_values(form):
    return (form["system"].strip(), form["method"].strip(), form["frequency"].strip(),
            form["retention"].strip(), form["location"].strip(), 1 if form["offsite"] else 0,
            _opt_user(form["owner_id"]), form["notes"].strip())


@router.post("/it/backups/add")
def backup_add(user=Depends(require_it),
               system: str = Form(...), method: str = Form(""), frequency: str = Form(""),
               retention: str = Form(""), location: str = Form(""), offsite: str = Form(""),
               owner_id: str = Form(""), notes: str = Form("")):
    db.execute(
        """INSERT INTO backups (system, method, frequency, retention, location, offsite, owner_id, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", _backup_values(locals()))
    return RedirectResponse("/it/backups?msg=Backup+added", status_code=303)


@router.get("/it/backups/{backup_id}/edit")
def backup_edit_form(request: Request, backup_id: int, user=Depends(require_it)):
    tests = db.query(
        """SELECT t.*, u.name AS tester FROM backup_tests t LEFT JOIN users u ON u.id = t.tested_by
           WHERE t.backup_id = ? ORDER BY t.tested_on DESC, t.id DESC""", (backup_id,))
    return page(request, "backup_edit.html", user=user, tab="backups",
                backup=_get_backup(backup_id), tests=tests, **_backup_ctx())


@router.post("/it/backups/{backup_id}/edit")
def backup_edit(backup_id: int, user=Depends(require_it),
                system: str = Form(...), method: str = Form(""), frequency: str = Form(""),
                retention: str = Form(""), location: str = Form(""), offsite: str = Form(""),
                owner_id: str = Form(""), notes: str = Form(""), active: str = Form("")):
    _get_backup(backup_id)
    db.execute(
        """UPDATE backups SET system = ?, method = ?, frequency = ?, retention = ?, location = ?,
           offsite = ?, owner_id = ?, notes = ?, active = ?, updated_at = datetime('now') WHERE id = ?""",
        _backup_values(locals()) + (1 if active else 0, backup_id))
    return RedirectResponse("/it/backups?msg=Backup+saved", status_code=303)


@router.post("/it/backups/{backup_id}/test")
def backup_test_add(backup_id: int, user=Depends(require_it),
                    tested_on: str = Form(""), result: str = Form(...), notes: str = Form("")):
    _get_backup(backup_id)
    db.execute(
        "INSERT INTO backup_tests (backup_id, tested_on, result, tested_by, notes) VALUES (?, ?, ?, ?, ?)",
        (backup_id, _opt_date(tested_on) or _today(), _check_enum(result, TEST_RESULTS, "result"),
         user["id"], notes.strip()))
    return RedirectResponse("/it/backups?msg=Restore+test+logged", status_code=303)


@router.post("/it/backups/tests/{test_id}/delete")
def backup_test_delete(test_id: int, user=Depends(require_it)):
    t = db.query_one("SELECT backup_id FROM backup_tests WHERE id = ?", (test_id,))
    if not t:
        raise HTTPException(404, "test not found")
    db.execute("DELETE FROM backup_tests WHERE id = ?", (test_id,))
    return RedirectResponse(f"/it/backups/{t['backup_id']}/edit?msg=Test+removed", status_code=303)


@router.post("/it/backups/{backup_id}/delete")
def backup_delete(backup_id: int, user=Depends(require_it)):
    _get_backup(backup_id)
    db.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
    return RedirectResponse("/it/backups?msg=Backup+deleted", status_code=303)


# ======================================================================
# Incident log
# ======================================================================

def _incident_rows(where="1=1", args=()):
    return db.query(
        f"""SELECT i.*, u.name AS lead FROM incidents i LEFT JOIN users u ON u.id = i.lead_id
            WHERE {where}
            ORDER BY CASE i.status WHEN 'open' THEN 0 WHEN 'contained' THEN 1
                                   WHEN 'resolved' THEN 2 ELSE 3 END,
                     CASE i.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                     WHEN 'medium' THEN 2 ELSE 3 END,
                     i.detected_on DESC, i.id DESC""", args)


def _get_incident(incident_id):
    row = db.query_one(
        """SELECT i.*, u.name AS lead, c.name AS reporter FROM incidents i
           LEFT JOIN users u ON u.id = i.lead_id LEFT JOIN users c ON c.id = i.created_by
           WHERE i.id = ?""", (incident_id,))
    if not row:
        raise HTTPException(404, "incident not found")
    return row


def _incident_ctx():
    return dict(severities=SEVERITIES, severity_labels=SEVERITY_LABELS,
                statuses=INCIDENT_STATUSES, status_labels=INCIDENT_STATUS_LABELS,
                people=_people(), today=_today())


@router.get("/it/incidents")
def incidents_list(request: Request, q: str = "", status: str = "active", severity: str = "",
                   user=Depends(require_it)):
    where, args = [], []
    q = q.strip()
    if q:
        like = f"%{q}%"
        where.append("(i.title LIKE ? OR i.systems LIKE ? OR i.summary LIKE ? OR i.timeline LIKE ?)")
        args += [like] * 4
    if status == "active":
        where.append("i.status != 'closed'")
    elif status:
        where.append("i.status = ?")
        args.append(_check_enum(status, INCIDENT_STATUSES, "status"))
    if severity:
        where.append("i.severity = ?")
        args.append(_check_enum(severity, SEVERITIES, "severity"))
    rows = _incident_rows(" AND ".join(where) or "1=1", tuple(args))
    year = _today()[:4]
    stats = db.query_one(
        """SELECT SUM(status != 'closed') AS open,
                  SUM(status != 'closed' AND severity IN ('high', 'critical')) AS urgent,
                  SUM(detected_on >= ?) AS this_year,
                  SUM(status = 'closed' AND detected_on >= ?) AS closed_year
           FROM incidents""", (year + "-01-01", year + "-01-01"))
    plan_docs = db.query(
        "SELECT id, title, status, review_date FROM it_docs WHERE section = 'ir' ORDER BY status, title")
    return page(request, "it_incidents.html", user=user, tab="incidents", rows=rows, q=q,
                status=status, severity=severity, stats=stats, year=year, plan_docs=plan_docs,
                **_incident_ctx())


@router.post("/it/incidents/add")
def incident_add(user=Depends(require_it),
                 title: str = Form(...), severity: str = Form("medium"), detected_on: str = Form(""),
                 lead_id: str = Form(""), systems: str = Form(""), summary: str = Form("")):
    iid = db.execute(
        """INSERT INTO incidents (title, severity, detected_on, lead_id, systems, summary, timeline, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title.strip(), _check_enum(severity, SEVERITIES, "severity"),
         _opt_date(detected_on) or _today(), _opt_user(lead_id), systems.strip(), summary.strip(),
         f"- {_today()} — Incident opened by {user['name']}.\n", user["id"]))
    return RedirectResponse(f"/it/incidents/{iid}", status_code=303)


@router.get("/it/incidents/{incident_id}")
def incident_view(request: Request, incident_id: int, user=Depends(require_it)):
    return page(request, "incident_view.html", user=user, tab="incidents",
                inc=_get_incident(incident_id), **_incident_ctx())


@router.get("/it/incidents/{incident_id}/edit")
def incident_edit_form(request: Request, incident_id: int, user=Depends(require_it)):
    return page(request, "incident_edit.html", user=user, tab="incidents",
                inc=_get_incident(incident_id), **_incident_ctx())


@router.post("/it/incidents/{incident_id}/edit")
def incident_edit(incident_id: int, user=Depends(require_it),
                  title: str = Form(...), severity: str = Form("medium"), status: str = Form("open"),
                  detected_on: str = Form(""), closed_on: str = Form(""), lead_id: str = Form(""),
                  systems: str = Form(""), summary: str = Form(""), timeline: str = Form(""),
                  lessons: str = Form("")):
    _get_incident(incident_id)
    status = _check_enum(status, INCIDENT_STATUSES, "status")
    closed_on = _opt_date(closed_on)
    if status == "closed" and not closed_on:
        closed_on = _today()
    if status != "closed":
        closed_on = ""
    db.execute(
        """UPDATE incidents SET title = ?, severity = ?, status = ?, detected_on = ?, closed_on = ?,
           lead_id = ?, systems = ?, summary = ?, timeline = ?, lessons = ?,
           updated_at = datetime('now') WHERE id = ?""",
        (title.strip(), _check_enum(severity, SEVERITIES, "severity"), status,
         _opt_date(detected_on) or _today(), closed_on, _opt_user(lead_id), systems.strip(),
         summary.strip(), timeline, lessons, incident_id))
    return RedirectResponse(f"/it/incidents/{incident_id}?msg=Saved", status_code=303)


@router.post("/it/incidents/{incident_id}/status")
def incident_status(incident_id: int, status: str = Form(...), user=Depends(require_it)):
    inc = _get_incident(incident_id)
    status = _check_enum(status, INCIDENT_STATUSES, "status")
    closed_on = _today() if status == "closed" else ""
    line = f"- {_today()} — Status set to {INCIDENT_STATUS_LABELS[status]} by {user['name']}.\n"
    timeline = inc["timeline"] + ("" if inc["timeline"].endswith("\n") or not inc["timeline"] else "\n") + line
    db.execute("""UPDATE incidents SET status = ?, closed_on = ?, timeline = ?,
                  updated_at = datetime('now') WHERE id = ?""",
               (status, closed_on, timeline, incident_id))
    return RedirectResponse(f"/it/incidents/{incident_id}", status_code=303)


@router.post("/it/incidents/{incident_id}/note")
def incident_note(incident_id: int, note: str = Form(...), user=Depends(require_it)):
    """Append a dated line to the timeline without opening the full editor."""
    inc = _get_incident(incident_id)
    note = note.strip()
    if not note:
        return RedirectResponse(f"/it/incidents/{incident_id}", status_code=303)
    line = f"- {_today()} — {note} ({user['name']})\n"
    timeline = inc["timeline"] + ("" if inc["timeline"].endswith("\n") or not inc["timeline"] else "\n") + line
    db.execute("UPDATE incidents SET timeline = ?, updated_at = datetime('now') WHERE id = ?",
               (timeline, incident_id))
    return RedirectResponse(f"/it/incidents/{incident_id}", status_code=303)


@router.post("/it/incidents/{incident_id}/delete")
def incident_delete(incident_id: int, user=Depends(require_it)):
    _get_incident(incident_id)
    db.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
    return RedirectResponse("/it/incidents?msg=Incident+deleted", status_code=303)


@router.get("/it/incidents/export/incidents.csv")
def incidents_export(user=Depends(require_it)):
    return _csv("incidents.csv",
                ["ID", "Title", "Severity", "Status", "Detected", "Closed", "Lead", "Systems", "Summary"],
                [[i["id"], i["title"], SEVERITY_LABELS[i["severity"]],
                  INCIDENT_STATUS_LABELS[i["status"]], i["detected_on"], i["closed_on"],
                  i["lead"] or "", i["systems"], i["summary"]]
                 for i in _incident_rows()])
