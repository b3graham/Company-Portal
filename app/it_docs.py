"""IT document engine: Policies & standards, Runbooks, and Disaster Recovery.
Markdown pages with full version history plus an owner, a status, and a review date
so stale documents surface on the IT home page. IT department only (and admins)."""
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db
from .it import _opt_date, _opt_user, _people, require_it
from .web import page

router = APIRouter()

SECTIONS = ["policies", "runbooks", "dr", "ir"]
SECTION_META = {
    "policies": {"label": "Policies & standards", "icon": "📜",
                 "blurb": "Rules everyone follows: acceptable use, passwords and MFA, "
                          "device baseline, data handling."},
    "runbooks": {"label": "Runbooks", "icon": "🧭",
                 "blurb": "Step-by-step how-tos: VPN setup, new device build, MFA enrollment."},
    "dr": {"label": "Disaster recovery", "icon": "🛟",
           "blurb": "What comes back first, how, and from where. Pairs with the Backups tab."},
    "ir": {"label": "Incident response plan", "icon": "📕",
           "blurb": "Severity levels, response steps, who to call, notification rules. "
                    "The incident log itself lives on the Incidents tab."},
}


def _tab(section):
    """Which IT tab lights up for a docs section (the IR plan sits under Incidents)."""
    return "incidents" if section == "ir" else section


DOC_STATUSES = ["draft", "active", "retired"]
DOC_STATUS_LABELS = {"draft": "Draft", "active": "Active", "retired": "Retired"}


def _section(value: str) -> str:
    if value not in SECTIONS:
        raise HTTPException(404, "no such section")
    return value


def _doc_status(value: str) -> str:
    if value not in DOC_STATUSES:
        raise HTTPException(422, "bad status")
    return value


def _get_doc(doc_id: int):
    doc = db.query_one(
        """SELECT d.*, o.name AS owner, e.name AS editor FROM it_docs d
           LEFT JOIN users o ON o.id = d.owner_id LEFT JOIN users e ON e.id = d.updated_by
           WHERE d.id = ?""", (doc_id,))
    if not doc:
        raise HTTPException(404, "document not found")
    return doc


def _ctx(section):
    return dict(section=section, meta=SECTION_META[section], sections=SECTIONS,
                section_meta=SECTION_META, statuses=DOC_STATUSES,
                status_labels=DOC_STATUS_LABELS, people=_people(),
                today=date.today().isoformat())


@router.get("/it/docs/{section}")
def docs_list(request: Request, section: str, q: str = "", show: str = "current",
              user=Depends(require_it)):
    section = _section(section)
    where, args = ["d.section = ?"], [section]
    q = q.strip()
    if q:
        where.append("(d.title LIKE ? OR d.content LIKE ?)")
        args += [f"%{q}%"] * 2
    if show != "all":
        where.append("d.status != 'retired'")
    rows = db.query(
        f"""SELECT d.*, o.name AS owner, e.name AS editor FROM it_docs d
            LEFT JOIN users o ON o.id = d.owner_id LEFT JOIN users e ON e.id = d.updated_by
            WHERE {' AND '.join(where)}
            ORDER BY CASE d.status WHEN 'active' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END, d.title""",
        tuple(args))
    return page(request, "it_docs.html", user=user, tab=_tab(section), rows=rows, q=q, show=show,
                **_ctx(section))


@router.get("/it/docs/{section}/new")
def doc_new_form(request: Request, section: str, user=Depends(require_it)):
    section = _section(section)
    return page(request, "it_doc_edit.html", user=user, tab=_tab(section), doc=None, **_ctx(section))


@router.post("/it/docs/{section}/new")
def doc_create(section: str, user=Depends(require_it),
               title: str = Form(...), content: str = Form(""), status: str = Form("draft"),
               owner_id: str = Form(""), review_date: str = Form("")):
    section = _section(section)
    doc_id = db.execute(
        """INSERT INTO it_docs (section, title, content, status, owner_id, review_date, updated_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (section, title.strip(), content, _doc_status(status), _opt_user(owner_id),
         _opt_date(review_date), user["id"]))
    db.execute("INSERT INTO it_doc_versions (doc_id, title, content, saved_by) VALUES (?, ?, ?, ?)",
               (doc_id, title.strip(), content, user["id"]))
    return RedirectResponse(f"/it/doc/{doc_id}", status_code=303)


@router.get("/it/doc/{doc_id}")
def doc_view(request: Request, doc_id: int, user=Depends(require_it)):
    doc = _get_doc(doc_id)
    versions = db.query(
        """SELECT v.id, v.saved_at, u.name AS author FROM it_doc_versions v
           LEFT JOIN users u ON u.id = v.saved_by WHERE v.doc_id = ? ORDER BY v.id DESC""",
        (doc_id,))
    return page(request, "it_doc_view.html", user=user, tab=_tab(doc["section"]), doc=doc,
                versions=versions, **_ctx(doc["section"]))


@router.get("/it/doc/{doc_id}/edit")
def doc_edit_form(request: Request, doc_id: int, user=Depends(require_it)):
    doc = _get_doc(doc_id)
    return page(request, "it_doc_edit.html", user=user, tab=_tab(doc["section"]), doc=doc,
                **_ctx(doc["section"]))


@router.post("/it/doc/{doc_id}/edit")
def doc_save(doc_id: int, user=Depends(require_it),
             title: str = Form(...), content: str = Form(""), status: str = Form("draft"),
             owner_id: str = Form(""), review_date: str = Form("")):
    doc = _get_doc(doc_id)
    db.execute(
        """UPDATE it_docs SET title = ?, content = ?, status = ?, owner_id = ?, review_date = ?,
           updated_by = ?, updated_at = datetime('now') WHERE id = ?""",
        (title.strip(), content, _doc_status(status), _opt_user(owner_id),
         _opt_date(review_date), user["id"], doc_id))
    if title.strip() != doc["title"] or content != doc["content"]:
        db.execute(
            "INSERT INTO it_doc_versions (doc_id, title, content, saved_by) VALUES (?, ?, ?, ?)",
            (doc_id, title.strip(), content, user["id"]))
    return RedirectResponse(f"/it/doc/{doc_id}?msg=Saved", status_code=303)


@router.post("/it/doc/{doc_id}/status")
def doc_status(doc_id: int, status: str = Form(...), user=Depends(require_it)):
    doc = _get_doc(doc_id)
    db.execute("UPDATE it_docs SET status = ?, updated_by = ?, updated_at = datetime('now') WHERE id = ?",
               (_doc_status(status), user["id"], doc_id))
    return RedirectResponse(f"/it/docs/{doc['section']}", status_code=303)


@router.post("/it/doc/{doc_id}/reviewed")
def doc_reviewed(doc_id: int, months: int = Form(12), user=Depends(require_it)):
    """Mark a doc reviewed today and push its next review date out by N months."""
    doc = _get_doc(doc_id)
    if months not in (3, 6, 12, 24):
        raise HTTPException(422, "bad review interval")
    today = date.today()
    m = today.month - 1 + months
    nxt = date(today.year + m // 12, m % 12 + 1, min(today.day, 28)).isoformat()
    db.execute("UPDATE it_docs SET review_date = ?, updated_by = ?, updated_at = datetime('now') WHERE id = ?",
               (nxt, user["id"], doc_id))
    return RedirectResponse(f"/it/doc/{doc_id}?msg=Next+review+set+to+{nxt}", status_code=303)


@router.get("/it/doc/{doc_id}/versions/{version_id}")
def doc_version_view(request: Request, doc_id: int, version_id: int, user=Depends(require_it)):
    doc = _get_doc(doc_id)
    version = db.query_one(
        """SELECT v.*, u.name AS author FROM it_doc_versions v LEFT JOIN users u ON u.id = v.saved_by
           WHERE v.id = ? AND v.doc_id = ?""", (version_id, doc_id))
    if not version:
        raise HTTPException(404, "version not found")
    return page(request, "it_doc_version.html", user=user, tab=_tab(doc["section"]), doc=doc,
                version=version, **_ctx(doc["section"]))


@router.post("/it/doc/{doc_id}/versions/{version_id}/restore")
def doc_version_restore(doc_id: int, version_id: int, user=Depends(require_it)):
    _get_doc(doc_id)
    version = db.query_one("SELECT * FROM it_doc_versions WHERE id = ? AND doc_id = ?",
                           (version_id, doc_id))
    if not version:
        raise HTTPException(404, "version not found")
    db.execute("""UPDATE it_docs SET title = ?, content = ?, updated_by = ?,
                  updated_at = datetime('now') WHERE id = ?""",
               (version["title"], version["content"], user["id"], doc_id))
    db.execute("INSERT INTO it_doc_versions (doc_id, title, content, saved_by) VALUES (?, ?, ?, ?)",
               (doc_id, version["title"], version["content"], user["id"]))
    return RedirectResponse(f"/it/doc/{doc_id}?msg=Version+restored", status_code=303)


@router.post("/it/doc/{doc_id}/delete")
def doc_delete(doc_id: int, user=Depends(require_it)):
    doc = _get_doc(doc_id)
    db.execute("DELETE FROM it_docs WHERE id = ?", (doc_id,))
    return RedirectResponse(f"/it/docs/{doc['section']}?msg=Document+deleted", status_code=303)
