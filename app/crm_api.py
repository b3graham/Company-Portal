"""Read-only JSON API over the CRM (contacts, leads, projects) for automation
such as the Paperclip Assistant agent.

Authentication is a bearer token from the CRM_API_TOKEN env var, checked
independently of the portal's session cookies. A session cookie grants nothing
here, and the token grants nothing anywhere else. Only GET routes exist, so
the token cannot create, change or delete a record. If CRM_API_TOKEN is unset
every route returns 404, so the API is invisible until deliberately enabled.
"""
import hmac
import os
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import db
from .crm import OPEN_STAGES, PROJECT_STATUSES, STAGES

router = APIRouter(prefix="/api/crm")

MAX_LIMIT = 500


def _token() -> str:
    return os.environ.get("CRM_API_TOKEN", "").strip()


def require_token(request: Request):
    expected = _token()
    if not expected:
        raise HTTPException(404)
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(presented.strip(), expected):
        raise HTTPException(401, "invalid or missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})


def _date(raw: str) -> str:
    raw = (raw or "").strip()
    if raw and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise HTTPException(422, "dates must be YYYY-MM-DD")
    return raw


def _page(limit: int, offset: int):
    if limit < 1 or limit > MAX_LIMIT:
        raise HTTPException(422, f"limit must be 1-{MAX_LIMIT}")
    if offset < 0:
        raise HTTPException(422, "offset must be >= 0")
    return limit, offset


CONTACT_SQL = """SELECT c.*,
                        (SELECT COUNT(*) FROM leads l WHERE l.contact_id = c.id) AS n_leads,
                        (SELECT COUNT(*) FROM projects p WHERE p.contact_id = c.id) AS n_projects
                 FROM contacts c"""

LEAD_SQL = """SELECT l.*, e.name AS entity, c.name AS contact_name, c.company AS contact_company,
                     u.name AS created_by_name
              FROM leads l
              LEFT JOIN expense_lists e ON e.id = l.entity_id
              LEFT JOIN contacts c ON c.id = l.contact_id
              LEFT JOIN users u ON u.id = l.created_by"""

PROJECT_SQL = """SELECT p.*, e.name AS entity, c.name AS contact_name, c.company AS contact_company,
                        l.title AS lead_title, u.name AS created_by_name
                 FROM projects p
                 LEFT JOIN expense_lists e ON e.id = p.entity_id
                 LEFT JOIN contacts c ON c.id = p.contact_id
                 LEFT JOIN leads l ON l.id = p.lead_id
                 LEFT JOIN users u ON u.id = p.created_by"""


def _rows(sql, args=()):
    return [dict(r) for r in db.query(sql, args)]


# ---------- contacts ----------

@router.get("/contacts", dependencies=[Depends(require_token)])
def contacts(q: str = "", limit: int = Query(200), offset: int = Query(0)):
    limit, offset = _page(limit, offset)
    q = q.strip()
    where, args = "1=1", ()
    if q:
        like = f"%{q}%"
        where = "(c.name LIKE ? OR c.company LIKE ? OR c.email LIKE ? OR c.address LIKE ? OR c.notes LIKE ?)"
        args = (like,) * 5
    items = _rows(f"{CONTACT_SQL} WHERE {where} ORDER BY c.name LIMIT ? OFFSET ?", args + (limit, offset))
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/contacts/{contact_id}", dependencies=[Depends(require_token)])
def contact(contact_id: int):
    row = db.query_one(f"{CONTACT_SQL} WHERE c.id = ?", (contact_id,))
    if not row:
        raise HTTPException(404, "no such contact")
    item = dict(row)
    item["leads"] = _rows(f"{LEAD_SQL} WHERE l.contact_id = ? ORDER BY l.updated_at DESC", (contact_id,))
    item["projects"] = _rows(f"{PROJECT_SQL} WHERE p.contact_id = ? ORDER BY p.updated_at DESC", (contact_id,))
    return item


# ---------- leads ----------

@router.get("/leads", dependencies=[Depends(require_token)])
def leads(stage: str = "", open: bool = False, due_by: str = "",
          limit: int = Query(200), offset: int = Query(0)):
    """Filters: stage=<one of STAGES>; open=true for the four open stages;
    due_by=YYYY-MM-DD for leads with a follow-up on or before that date."""
    limit, offset = _page(limit, offset)
    clauses, args = [], []
    stage = stage.strip()
    if stage:
        if stage not in STAGES:
            raise HTTPException(422, f"stage must be one of {', '.join(STAGES)}")
        clauses.append("l.stage = ?")
        args.append(stage)
    if open:
        clauses.append(f"l.stage IN ({','.join('?' * len(OPEN_STAGES))})")
        args.extend(OPEN_STAGES)
    due_by = _date(due_by)
    if due_by:
        clauses.append("l.follow_up != '' AND l.follow_up <= ?")
        args.append(due_by)
    where = " AND ".join(clauses) or "1=1"
    items = _rows(f"{LEAD_SQL} WHERE {where} ORDER BY l.updated_at DESC LIMIT ? OFFSET ?",
                  tuple(args) + (limit, offset))
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/leads/{lead_id}", dependencies=[Depends(require_token)])
def lead(lead_id: int):
    row = db.query_one(f"{LEAD_SQL} WHERE l.id = ?", (lead_id,))
    if not row:
        raise HTTPException(404, "no such lead")
    item = dict(row)
    item["projects"] = _rows(f"{PROJECT_SQL} WHERE p.lead_id = ? ORDER BY p.updated_at DESC", (lead_id,))
    return item


# ---------- projects ----------

@router.get("/projects", dependencies=[Depends(require_token)])
def projects(status: str = "", due_by: str = "",
             limit: int = Query(200), offset: int = Query(0)):
    limit, offset = _page(limit, offset)
    clauses, args = [], []
    status = status.strip()
    if status:
        if status not in PROJECT_STATUSES:
            raise HTTPException(422, f"status must be one of {', '.join(PROJECT_STATUSES)}")
        clauses.append("p.status = ?")
        args.append(status)
    due_by = _date(due_by)
    if due_by:
        clauses.append("p.due_date != '' AND p.due_date <= ?")
        args.append(due_by)
    where = " AND ".join(clauses) or "1=1"
    items = _rows(f"{PROJECT_SQL} WHERE {where} ORDER BY p.updated_at DESC LIMIT ? OFFSET ?",
                  tuple(args) + (limit, offset))
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/projects/{project_id}", dependencies=[Depends(require_token)])
def project(project_id: int):
    row = db.query_one(f"{PROJECT_SQL} WHERE p.id = ?", (project_id,))
    if not row:
        raise HTTPException(404, "no such project")
    return dict(row)


# ---------- summary ----------

@router.get("/summary", dependencies=[Depends(require_token)])
def summary():
    """Pipeline counts plus follow-ups and project due dates that are today or overdue."""
    today = date.today().isoformat()
    by_stage = {s: 0 for s in STAGES}
    for r in db.query("SELECT stage, COUNT(*) AS n FROM leads GROUP BY stage"):
        by_stage[r["stage"]] = r["n"]
    by_status = {s: 0 for s in PROJECT_STATUSES}
    for r in db.query("SELECT status, COUNT(*) AS n FROM projects GROUP BY status"):
        by_status[r["status"]] = r["n"]
    open_marks = ",".join("?" * len(OPEN_STAGES))
    follow_ups = _rows(
        f"{LEAD_SQL} WHERE l.stage IN ({open_marks}) AND l.follow_up != '' AND l.follow_up <= ? "
        "ORDER BY l.follow_up", tuple(OPEN_STAGES) + (today,))
    due_projects = _rows(
        f"{PROJECT_SQL} WHERE p.status != 'done' AND p.due_date != '' AND p.due_date <= ? "
        "ORDER BY p.due_date", (today,))
    return {
        "as_of": today,
        "contacts": db.query_one("SELECT COUNT(*) AS n FROM contacts")["n"],
        "leads_by_stage": by_stage,
        "projects_by_status": by_status,
        "follow_ups_due": follow_ups,
        "projects_due": due_projects,
    }
