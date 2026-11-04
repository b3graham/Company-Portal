"""CRM: lead pipeline board, key contacts, and a project kanban board.
Open to all portal users, like Tasks and Docs. Won leads convert to projects."""
import re
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db
from .auth import require_user
from .web import page

router = APIRouter()

STAGES = ["new", "contacted", "qualified", "proposal", "won", "lost"]
OPEN_STAGES = ["new", "contacted", "qualified", "proposal"]
STAGE_LABELS = {"new": "New", "contacted": "Contacted", "qualified": "Qualified",
                "proposal": "Proposal", "won": "Won", "lost": "Lost"}

PROJECT_STATUSES = ["planned", "active", "hold", "done"]
PROJECT_LABELS = {"planned": "Planned", "active": "In Progress",
                  "hold": "On Hold", "done": "Complete"}


def _entities():
    return db.query("SELECT id, name FROM expense_lists WHERE kind = 'entity' AND active = 1 ORDER BY name")


def _contacts_brief():
    return db.query("SELECT id, name, company FROM contacts ORDER BY name")


def _opt_id(raw: str, table: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.isdigit() or not db.query_one(f"SELECT 1 FROM {table} WHERE id = ?", (int(raw),)):
        raise HTTPException(422, f"bad {table} id")
    return int(raw)


def _opt_date(raw: str) -> str:
    raw = (raw or "").strip()
    if raw and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise HTTPException(422, "dates must be YYYY-MM-DD")
    return raw


# ---------- leads ----------

def _lead_rows(where="1=1", args=()):
    return db.query(
        f"""SELECT l.*, e.name AS entity, c.name AS contact_name, c.company AS contact_company
            FROM leads l
            LEFT JOIN expense_lists e ON e.id = l.entity_id
            LEFT JOIN contacts c ON c.id = l.contact_id
            WHERE {where} ORDER BY l.updated_at DESC""", args)


@router.get("/crm")
def crm_home():
    return RedirectResponse("/crm/leads", status_code=303)


@router.get("/crm/leads")
def leads_board(request: Request, user=Depends(require_user)):
    leads = _lead_rows()
    today = date.today().isoformat()
    open_leads = [l for l in leads if l["stage"] in OPEN_STAGES]
    columns = {s: [l for l in open_leads if l["stage"] == s] for s in OPEN_STAGES}
    closed = [l for l in leads if l["stage"] in ("won", "lost")]
    overdue = sum(1 for l in open_leads if l["follow_up"] and l["follow_up"] < today)
    year = today[:4]
    won = sum(1 for l in closed if l["stage"] == "won" and (l["closed_at"] or "").startswith(year))
    lost = sum(1 for l in closed if l["stage"] == "lost" and (l["closed_at"] or "").startswith(year))
    sources = [r["source"] for r in db.query(
        "SELECT DISTINCT source FROM leads WHERE source != '' ORDER BY source")]
    converted = {r["lead_id"] for r in db.query(
        "SELECT lead_id FROM projects WHERE lead_id IS NOT NULL")}
    return page(request, "crm_leads.html", user=user, tab="leads",
                columns=columns, closed=closed, converted=converted, today=today,
                stage_labels=STAGE_LABELS, open_stages=OPEN_STAGES,
                n_open=len(open_leads), overdue=overdue, won=won, lost=lost, year=year,
                entities=_entities(), contacts=_contacts_brief(), sources=sources)


@router.post("/crm/leads/add")
def lead_add(user=Depends(require_user),
             title: str = Form(...), entity_id: str = Form(""), contact_id: str = Form(""),
             source: str = Form(""), follow_up: str = Form(""), notes: str = Form("")):
    db.execute(
        """INSERT INTO leads (title, entity_id, contact_id, source, follow_up, notes, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (title.strip(), _opt_id(entity_id, "expense_lists"), _opt_id(contact_id, "contacts"),
         source.strip(), _opt_date(follow_up), notes.strip(), user["id"]))
    return RedirectResponse("/crm/leads", status_code=303)


def _get_lead(lead_id: int):
    lead = db.query_one("SELECT * FROM leads WHERE id = ?", (lead_id,))
    if not lead:
        raise HTTPException(404, "lead not found")
    return lead


@router.post("/crm/leads/{lead_id}/stage")
def lead_stage(lead_id: int, stage: str = Form(...), user=Depends(require_user)):
    _get_lead(lead_id)
    if stage not in STAGES:
        raise HTTPException(422, "bad stage")
    closed_at = date.today().isoformat() if stage in ("won", "lost") else None
    db.execute("""UPDATE leads SET stage = ?, closed_at = ?, updated_at = datetime('now')
                  WHERE id = ?""", (stage, closed_at, lead_id))
    return RedirectResponse("/crm/leads", status_code=303)


@router.get("/crm/leads/{lead_id}/edit")
def lead_edit_form(request: Request, lead_id: int, user=Depends(require_user)):
    sources = [r["source"] for r in db.query(
        "SELECT DISTINCT source FROM leads WHERE source != '' ORDER BY source")]
    return page(request, "lead_edit.html", user=user, lead=_get_lead(lead_id),
                entities=_entities(), contacts=_contacts_brief(), sources=sources,
                stages=STAGES, stage_labels=STAGE_LABELS)


@router.post("/crm/leads/{lead_id}/edit")
def lead_edit(lead_id: int, user=Depends(require_user),
              title: str = Form(...), stage: str = Form(...), entity_id: str = Form(""),
              contact_id: str = Form(""), source: str = Form(""),
              follow_up: str = Form(""), notes: str = Form("")):
    lead = _get_lead(lead_id)
    if stage not in STAGES:
        raise HTTPException(422, "bad stage")
    closed_at = lead["closed_at"]
    if stage in ("won", "lost"):
        closed_at = closed_at or date.today().isoformat()
    else:
        closed_at = None
    db.execute(
        """UPDATE leads SET title = ?, stage = ?, entity_id = ?, contact_id = ?, source = ?,
           follow_up = ?, notes = ?, closed_at = ?, updated_at = datetime('now') WHERE id = ?""",
        (title.strip(), stage, _opt_id(entity_id, "expense_lists"), _opt_id(contact_id, "contacts"),
         source.strip(), _opt_date(follow_up), notes.strip(), closed_at, lead_id))
    return RedirectResponse("/crm/leads", status_code=303)


@router.post("/crm/leads/{lead_id}/delete")
def lead_delete(lead_id: int, user=Depends(require_user)):
    _get_lead(lead_id)
    db.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    return RedirectResponse("/crm/leads?msg=Lead+deleted", status_code=303)


@router.post("/crm/leads/{lead_id}/convert")
def lead_convert(lead_id: int, user=Depends(require_user)):
    lead = _get_lead(lead_id)
    if db.query_one("SELECT 1 FROM projects WHERE lead_id = ?", (lead_id,)):
        return RedirectResponse("/crm/projects?msg=Already+converted+to+a+project", status_code=303)
    db.execute("""UPDATE leads SET stage = 'won', closed_at = COALESCE(closed_at, ?),
                  updated_at = datetime('now') WHERE id = ?""",
               (date.today().isoformat(), lead_id))
    db.execute(
        """INSERT INTO projects (name, entity_id, contact_id, lead_id, notes, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (lead["title"], lead["entity_id"], lead["contact_id"], lead_id, lead["notes"], user["id"]))
    return RedirectResponse("/crm/projects?msg=Lead+converted+to+project", status_code=303)


# ---------- contacts ----------

@router.get("/crm/contacts")
def contacts_list(request: Request, q: str = "", user=Depends(require_user)):
    q = q.strip()
    if q:
        like = f"%{q}%"
        rows = db.query("""SELECT * FROM contacts WHERE name LIKE ? OR company LIKE ?
                           OR email LIKE ? OR address LIKE ? OR notes LIKE ? ORDER BY name""",
                        (like,) * 5)
    else:
        rows = db.query("SELECT * FROM contacts ORDER BY name")
    lead_counts = {r["contact_id"]: r["n"] for r in db.query(
        "SELECT contact_id, COUNT(*) AS n FROM leads WHERE contact_id IS NOT NULL GROUP BY contact_id")}
    project_counts = {r["contact_id"]: r["n"] for r in db.query(
        "SELECT contact_id, COUNT(*) AS n FROM projects WHERE contact_id IS NOT NULL GROUP BY contact_id")}
    rows = [dict(r) | {"n_leads": lead_counts.get(r["id"], 0),
                       "n_projects": project_counts.get(r["id"], 0)} for r in rows]
    return page(request, "crm_contacts.html", user=user, tab="contacts", q=q, rows=rows)


@router.post("/crm/contacts/add")
def contact_add(user=Depends(require_user),
                name: str = Form(...), company: str = Form(""), title: str = Form(""),
                email: str = Form(""), phone: str = Form(""), address: str = Form(""),
                notes: str = Form("")):
    db.execute(
        """INSERT INTO contacts (name, company, title, email, phone, address, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name.strip(), company.strip(), title.strip(), email.strip(), phone.strip(),
         address.strip(), notes.strip()))
    return RedirectResponse("/crm/contacts", status_code=303)


def _get_contact(contact_id: int):
    contact = db.query_one("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    if not contact:
        raise HTTPException(404, "contact not found")
    return contact


@router.get("/crm/contacts/{contact_id}/edit")
def contact_edit_form(request: Request, contact_id: int, user=Depends(require_user)):
    contact = _get_contact(contact_id)
    leads = _lead_rows("l.contact_id = ?", (contact_id,))
    projects = db.query("SELECT * FROM projects WHERE contact_id = ? ORDER BY updated_at DESC",
                        (contact_id,))
    return page(request, "contact_edit.html", user=user, contact=contact,
                leads=leads, projects=projects,
                stage_labels=STAGE_LABELS, project_labels=PROJECT_LABELS)


@router.post("/crm/contacts/{contact_id}/edit")
def contact_edit(contact_id: int, user=Depends(require_user),
                 name: str = Form(...), company: str = Form(""), title: str = Form(""),
                 email: str = Form(""), phone: str = Form(""), address: str = Form(""),
                 notes: str = Form("")):
    _get_contact(contact_id)
    db.execute(
        """UPDATE contacts SET name = ?, company = ?, title = ?, email = ?, phone = ?,
           address = ?, notes = ?
           WHERE id = ?""",
        (name.strip(), company.strip(), title.strip(), email.strip(), phone.strip(),
         address.strip(), notes.strip(), contact_id))
    return RedirectResponse("/crm/contacts?msg=Contact+saved", status_code=303)


@router.post("/crm/contacts/{contact_id}/delete")
def contact_delete(contact_id: int, user=Depends(require_user)):
    _get_contact(contact_id)
    db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    return RedirectResponse("/crm/contacts?msg=Contact+deleted", status_code=303)


# ---------- projects ----------

@router.get("/crm/projects")
def projects_board(request: Request, user=Depends(require_user)):
    rows = db.query(
        """SELECT p.*, e.name AS entity, c.name AS contact_name
           FROM projects p
           LEFT JOIN expense_lists e ON e.id = p.entity_id
           LEFT JOIN contacts c ON c.id = p.contact_id
           ORDER BY p.updated_at DESC""")
    columns = {s: [p for p in rows if p["status"] == s] for s in PROJECT_STATUSES}
    return page(request, "crm_projects.html", user=user, tab="projects",
                columns=columns, statuses=PROJECT_STATUSES, project_labels=PROJECT_LABELS,
                today=date.today().isoformat(),
                entities=_entities(), contacts=_contacts_brief())


@router.post("/crm/projects/add")
def project_add(user=Depends(require_user),
                name: str = Form(...), entity_id: str = Form(""), contact_id: str = Form(""),
                due_date: str = Form(""), notes: str = Form("")):
    db.execute(
        """INSERT INTO projects (name, entity_id, contact_id, due_date, notes, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name.strip(), _opt_id(entity_id, "expense_lists"), _opt_id(contact_id, "contacts"),
         _opt_date(due_date), notes.strip(), user["id"]))
    return RedirectResponse("/crm/projects", status_code=303)


def _get_project(project_id: int):
    project = db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not project:
        raise HTTPException(404, "project not found")
    return project


@router.post("/crm/projects/{project_id}/move")
def project_move(project_id: int, direction: str = Form(...), user=Depends(require_user)):
    project = _get_project(project_id)
    i = PROJECT_STATUSES.index(project["status"]) + (1 if direction == "next" else -1)
    if 0 <= i < len(PROJECT_STATUSES):
        db.execute("UPDATE projects SET status = ?, updated_at = datetime('now') WHERE id = ?",
                   (PROJECT_STATUSES[i], project_id))
    return RedirectResponse("/crm/projects", status_code=303)


@router.get("/crm/projects/{project_id}/edit")
def project_edit_form(request: Request, project_id: int, user=Depends(require_user)):
    return page(request, "project_edit.html", user=user, project=_get_project(project_id),
                entities=_entities(), contacts=_contacts_brief(),
                statuses=PROJECT_STATUSES, project_labels=PROJECT_LABELS)


@router.post("/crm/projects/{project_id}/edit")
def project_edit(project_id: int, user=Depends(require_user),
                 name: str = Form(...), status: str = Form(...), entity_id: str = Form(""),
                 contact_id: str = Form(""), due_date: str = Form(""), notes: str = Form("")):
    _get_project(project_id)
    if status not in PROJECT_STATUSES:
        raise HTTPException(422, "bad status")
    db.execute(
        """UPDATE projects SET name = ?, status = ?, entity_id = ?, contact_id = ?, due_date = ?,
           notes = ?, updated_at = datetime('now') WHERE id = ?""",
        (name.strip(), status, _opt_id(entity_id, "expense_lists"), _opt_id(contact_id, "contacts"),
         _opt_date(due_date), notes.strip(), project_id))
    return RedirectResponse("/crm/projects?msg=Project+saved", status_code=303)


@router.post("/crm/projects/{project_id}/delete")
def project_delete(project_id: int, user=Depends(require_user)):
    _get_project(project_id)
    db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return RedirectResponse("/crm/projects?msg=Project+deleted", status_code=303)
