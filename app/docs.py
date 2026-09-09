"""Collaboration area: folders with per-user access control, markdown docs
with version history, and file uploads."""
import os
import re
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from . import db
from .auth import require_user
from .web import page, render_md

router = APIRouter()

CATEGORIES = ("working", "company")
MAX_UPLOAD = 50 * 1024 * 1024  # 50 MB


def _cat(value: str) -> str:
    if value not in CATEGORIES:
        raise HTTPException(422, "category must be 'working' or 'company'")
    return value


# ---------- folder access ----------

def _get_folder(folder_id: int):
    folder = db.query_one("SELECT * FROM folders WHERE id = ?", (folder_id,))
    if not folder:
        raise HTTPException(404, "folder not found")
    return folder


def _can_access(user, folder) -> bool:
    if folder is None or not folder["restricted"]:
        return True
    if user["role"] == "admin" or folder["created_by"] == user["id"]:
        return True
    return bool(db.query_one("SELECT 1 FROM folder_access WHERE folder_id = ? AND user_id = ?",
                             (folder["id"], user["id"])))


def _can_manage(user, folder) -> bool:
    return user["role"] == "admin" or folder["created_by"] == user["id"]


def _check_folder_access(user, folder_id):
    """Return the folder row (or None for root), raising 403 if user lacks access."""
    if folder_id is None:
        return None
    folder = _get_folder(folder_id)
    if not _can_access(user, folder):
        raise HTTPException(403, "you do not have access to this folder")
    return folder


def _parse_folder_id(raw: str):
    raw = (raw or "").strip()
    if raw in ("", "0", "root"):
        return None
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(422, "bad folder id")


def _accessible_folders(user, cat):
    rows = db.query("SELECT * FROM folders WHERE category = ? ORDER BY name", (cat,))
    return [f for f in rows if _can_access(user, f)]


# ---------- listing ----------

@router.get("/docs")
def docs_list(request: Request, cat: str = "working", q: str = "", user=Depends(require_user)):
    cat = _cat(cat)
    if q:
        like = f"%{q}%"
        docs = db.query(
            """SELECT d.*, u.name AS editor, f.name AS folder_name, f.restricted, f.created_by AS folder_owner
               FROM docs d LEFT JOIN users u ON u.id = d.updated_by
               LEFT JOIN folders f ON f.id = d.folder_id
               WHERE d.category = ? AND (d.title LIKE ? OR d.content LIKE ?)
               ORDER BY d.updated_at DESC""", (cat, like, like))
        files = db.query(
            """SELECT fl.*, u.name AS uploader, f.name AS folder_name, f.restricted, f.created_by AS folder_owner
               FROM files fl LEFT JOIN users u ON u.id = fl.uploaded_by
               LEFT JOIN folders f ON f.id = fl.folder_id
               WHERE fl.category = ? AND fl.orig_name LIKE ?
               ORDER BY fl.uploaded_at DESC""", (cat, like))
        ok = {f["id"] for f in _accessible_folders(user, cat)}
        docs = [d for d in docs if d["folder_id"] is None or d["folder_id"] in ok]
        files = [f for f in files if f["folder_id"] is None or f["folder_id"] in ok]
        return page(request, "docs_list.html", user=user, cat=cat, q=q,
                    docs=docs, files=files, folders=[], move_targets=[])

    folders = _accessible_folders(user, cat)
    doc_counts = {r["folder_id"]: r["n"] for r in db.query(
        "SELECT folder_id, COUNT(*) AS n FROM docs WHERE folder_id IS NOT NULL GROUP BY folder_id")}
    file_counts = {r["folder_id"]: r["n"] for r in db.query(
        "SELECT folder_id, COUNT(*) AS n FROM files WHERE folder_id IS NOT NULL GROUP BY folder_id")}
    folders = [dict(f) | {"n_docs": doc_counts.get(f["id"], 0), "n_files": file_counts.get(f["id"], 0)}
               for f in folders]
    docs = db.query(
        """SELECT d.*, u.name AS editor FROM docs d LEFT JOIN users u ON u.id = d.updated_by
           WHERE d.category = ? AND d.folder_id IS NULL ORDER BY d.updated_at DESC""", (cat,))
    files = db.query(
        """SELECT f.*, u.name AS uploader FROM files f LEFT JOIN users u ON u.id = f.uploaded_by
           WHERE f.category = ? AND f.folder_id IS NULL ORDER BY f.uploaded_at DESC""", (cat,))
    return page(request, "docs_list.html", user=user, cat=cat, q=q,
                docs=docs, files=files, folders=folders, move_targets=folders)


# ---------- folders ----------

@router.post("/folders")
def folder_create(cat: str = Form(...), name: str = Form(...), restricted: str = Form(""),
                  user=Depends(require_user)):
    folder_id = db.execute(
        "INSERT INTO folders (name, category, restricted, created_by) VALUES (?, ?, ?, ?)",
        (name.strip(), _cat(cat), 1 if restricted else 0, user["id"]))
    return RedirectResponse(f"/folders/{folder_id}", status_code=303)


@router.get("/folders/{folder_id}")
def folder_view(request: Request, folder_id: int, user=Depends(require_user)):
    folder = _check_folder_access(user, folder_id)
    docs = db.query(
        """SELECT d.*, u.name AS editor FROM docs d LEFT JOIN users u ON u.id = d.updated_by
           WHERE d.folder_id = ? ORDER BY d.updated_at DESC""", (folder_id,))
    files = db.query(
        """SELECT f.*, u.name AS uploader FROM files f LEFT JOIN users u ON u.id = f.uploaded_by
           WHERE f.folder_id = ? ORDER BY f.uploaded_at DESC""", (folder_id,))
    members = db.query(
        """SELECT u.id, u.name FROM folder_access a JOIN users u ON u.id = a.user_id
           WHERE a.folder_id = ? AND u.active = 1 ORDER BY u.name""", (folder_id,))
    member_ids = {m["id"] for m in members}
    addable = [u for u in db.query("SELECT id, name FROM users WHERE active = 1 ORDER BY name")
               if u["id"] not in member_ids and u["id"] != folder["created_by"]]
    owner = db.query_one("SELECT name FROM users WHERE id = ?", (folder["created_by"],))
    move_targets = [f for f in _accessible_folders(user, folder["category"]) if f["id"] != folder_id]
    return page(request, "folder.html", user=user, folder=folder, docs=docs, files=files,
                members=members, addable=addable, owner=owner["name"] if owner else None,
                can_manage=_can_manage(user, folder), move_targets=move_targets)


@router.post("/folders/{folder_id}/restrict")
def folder_toggle_restrict(folder_id: int, user=Depends(require_user)):
    folder = _get_folder(folder_id)
    if not _can_manage(user, folder):
        raise HTTPException(403, "only the folder creator or an admin can change access")
    db.execute("UPDATE folders SET restricted = 1 - restricted WHERE id = ?", (folder_id,))
    return RedirectResponse(f"/folders/{folder_id}", status_code=303)


@router.post("/folders/{folder_id}/access/add")
def folder_grant(folder_id: int, user_id: int = Form(...), user=Depends(require_user)):
    folder = _get_folder(folder_id)
    if not _can_manage(user, folder):
        raise HTTPException(403, "only the folder creator or an admin can change access")
    if not db.query_one("SELECT 1 FROM users WHERE id = ? AND active = 1", (user_id,)):
        raise HTTPException(404, "user not found")
    db.execute("INSERT OR IGNORE INTO folder_access (folder_id, user_id) VALUES (?, ?)",
               (folder_id, user_id))
    return RedirectResponse(f"/folders/{folder_id}", status_code=303)


@router.post("/folders/{folder_id}/access/remove/{user_id}")
def folder_revoke(folder_id: int, user_id: int, user=Depends(require_user)):
    folder = _get_folder(folder_id)
    if not _can_manage(user, folder):
        raise HTTPException(403, "only the folder creator or an admin can change access")
    db.execute("DELETE FROM folder_access WHERE folder_id = ? AND user_id = ?", (folder_id, user_id))
    return RedirectResponse(f"/folders/{folder_id}", status_code=303)


@router.post("/folders/{folder_id}/delete")
def folder_delete(folder_id: int, user=Depends(require_user)):
    folder = _get_folder(folder_id)
    if not _can_manage(user, folder):
        raise HTTPException(403, "only the folder creator or an admin can delete this folder")
    if db.query_one("SELECT 1 FROM docs WHERE folder_id = ?", (folder_id,)) or \
       db.query_one("SELECT 1 FROM files WHERE folder_id = ?", (folder_id,)):
        return RedirectResponse(
            f"/folders/{folder_id}?msg=Folder+must+be+empty+before+deleting", status_code=303)
    db.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    return RedirectResponse(f"/docs?cat={folder['category']}&msg=Folder+deleted", status_code=303)


# ---------- docs ----------

@router.get("/docs/new")
def doc_new_form(request: Request, cat: str = "working", folder: int | None = None,
                 user=Depends(require_user)):
    cat = _cat(cat)
    if folder is not None:
        _check_folder_access(user, folder)
    return page(request, "doc_edit.html", user=user, doc=None, cat=cat,
                folders=_accessible_folders(user, cat), preselect=folder)


@router.post("/docs/new")
def doc_create(cat: str = Form(...), title: str = Form(...), content: str = Form(""),
               folder_id: str = Form(""), user=Depends(require_user)):
    cat = _cat(cat)
    fid = _parse_folder_id(folder_id)
    folder = _check_folder_access(user, fid)
    if folder and folder["category"] != cat:
        raise HTTPException(422, "folder belongs to a different category")
    doc_id = db.execute(
        "INSERT INTO docs (title, category, content, folder_id, updated_by) VALUES (?, ?, ?, ?, ?)",
        (title.strip(), cat, content, fid, user["id"]))
    db.execute("INSERT INTO doc_versions (doc_id, title, content, saved_by) VALUES (?, ?, ?, ?)",
               (doc_id, title.strip(), content, user["id"]))
    return RedirectResponse(f"/docs/{doc_id}", status_code=303)


def _get_doc(doc_id: int, user):
    doc = db.query_one(
        """SELECT d.*, u.name AS editor, f.name AS folder_name
           FROM docs d LEFT JOIN users u ON u.id = d.updated_by
           LEFT JOIN folders f ON f.id = d.folder_id WHERE d.id = ?""", (doc_id,))
    if not doc:
        raise HTTPException(404, "document not found")
    _check_folder_access(user, doc["folder_id"])
    return doc


@router.get("/docs/{doc_id}")
def doc_view(request: Request, doc_id: int, user=Depends(require_user)):
    doc = _get_doc(doc_id, user)
    versions = db.query(
        """SELECT v.id, v.saved_at, u.name AS author FROM doc_versions v
           LEFT JOIN users u ON u.id = v.saved_by WHERE v.doc_id = ? ORDER BY v.id DESC""",
        (doc_id,))
    return page(request, "doc_view.html", user=user, doc=doc, versions=versions)


@router.get("/docs/{doc_id}/edit")
def doc_edit_form(request: Request, doc_id: int, user=Depends(require_user)):
    doc = _get_doc(doc_id, user)
    return page(request, "doc_edit.html", user=user, doc=doc, cat=doc["category"],
                folders=_accessible_folders(user, doc["category"]), preselect=None)


@router.post("/docs/{doc_id}/edit")
def doc_save(doc_id: int, title: str = Form(...), content: str = Form(""),
             folder_id: str = Form(""), user=Depends(require_user)):
    doc = _get_doc(doc_id, user)
    fid = _parse_folder_id(folder_id)
    folder = _check_folder_access(user, fid)
    if folder and folder["category"] != doc["category"]:
        raise HTTPException(422, "folder belongs to a different category")
    db.execute("""UPDATE docs SET title = ?, content = ?, folder_id = ?, updated_by = ?,
                  updated_at = datetime('now') WHERE id = ?""",
               (title.strip(), content, fid, user["id"], doc_id))
    db.execute("INSERT INTO doc_versions (doc_id, title, content, saved_by) VALUES (?, ?, ?, ?)",
               (doc_id, title.strip(), content, user["id"]))
    return RedirectResponse(f"/docs/{doc_id}", status_code=303)


@router.get("/docs/{doc_id}/versions/{version_id}")
def doc_version_view(request: Request, doc_id: int, version_id: int, user=Depends(require_user)):
    doc = _get_doc(doc_id, user)
    version = db.query_one(
        """SELECT v.*, u.name AS author FROM doc_versions v LEFT JOIN users u ON u.id = v.saved_by
           WHERE v.id = ? AND v.doc_id = ?""", (version_id, doc_id))
    if not version:
        raise HTTPException(404, "version not found")
    return page(request, "doc_version.html", user=user, doc=doc, version=version)


@router.post("/docs/{doc_id}/versions/{version_id}/restore")
def doc_version_restore(doc_id: int, version_id: int, user=Depends(require_user)):
    _get_doc(doc_id, user)
    version = db.query_one("SELECT * FROM doc_versions WHERE id = ? AND doc_id = ?", (version_id, doc_id))
    if not version:
        raise HTTPException(404, "version not found")
    db.execute("UPDATE docs SET title = ?, content = ?, updated_by = ?, updated_at = datetime('now') WHERE id = ?",
               (version["title"], version["content"], user["id"], doc_id))
    db.execute("INSERT INTO doc_versions (doc_id, title, content, saved_by) VALUES (?, ?, ?, ?)",
               (doc_id, version["title"], version["content"], user["id"]))
    return RedirectResponse(f"/docs/{doc_id}", status_code=303)


@router.post("/docs/{doc_id}/delete")
def doc_delete(doc_id: int, user=Depends(require_user)):
    doc = _get_doc(doc_id, user)
    db.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
    if doc["folder_id"]:
        return RedirectResponse(f"/folders/{doc['folder_id']}?msg=Document+deleted", status_code=303)
    return RedirectResponse(f"/docs?cat={doc['category']}&msg=Document+deleted", status_code=303)


@router.post("/docs/preview")
def doc_preview(content: str = Form(""), user=Depends(require_user)):
    return {"html": render_md(content)}


# ---------- file uploads ----------

@router.post("/files/upload")
async def file_upload(request: Request, upload: UploadFile, cat: str = Form(...),
                      folder_id: str = Form(""), user=Depends(require_user)):
    cat = _cat(cat)
    fid = _parse_folder_id(folder_id)
    folder = _check_folder_access(user, fid)
    if folder and folder["category"] != cat:
        raise HTTPException(422, "folder belongs to a different category")
    orig = os.path.basename(upload.filename or "file")
    ext = os.path.splitext(orig)[1]
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", ext):
        ext = ""
    stored = secrets.token_hex(12) + ext.lower()
    path = os.path.join(db.UPLOAD_DIR, stored)
    size = 0
    with open(path, "wb") as out:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                os.remove(path)
                raise HTTPException(413, "file exceeds 50 MB limit")
            out.write(chunk)
    db.execute(
        "INSERT INTO files (orig_name, stored_name, category, size, folder_id, uploaded_by) VALUES (?, ?, ?, ?, ?, ?)",
        (orig, stored, cat, size, fid, user["id"]))
    if fid:
        return RedirectResponse(f"/folders/{fid}", status_code=303)
    return RedirectResponse(f"/docs?cat={cat}", status_code=303)


def _get_file(file_id: int, user):
    f = db.query_one("SELECT * FROM files WHERE id = ?", (file_id,))
    if not f:
        raise HTTPException(404, "file not found")
    _check_folder_access(user, f["folder_id"])
    return f


@router.get("/files/{file_id}/download")
def file_download(file_id: int, user=Depends(require_user)):
    f = _get_file(file_id, user)
    path = os.path.join(db.UPLOAD_DIR, f["stored_name"])
    if not os.path.isfile(path):
        raise HTTPException(410, "file missing from disk")
    return FileResponse(path, filename=f["orig_name"])


@router.post("/files/{file_id}/move")
def file_move(file_id: int, folder_id: str = Form(""), user=Depends(require_user)):
    f = _get_file(file_id, user)
    if (folder_id or "").strip() == "":
        return RedirectResponse(f"/docs?cat={f['category']}", status_code=303)  # placeholder pick
    fid = _parse_folder_id(folder_id)
    folder = _check_folder_access(user, fid)
    if folder and folder["category"] != f["category"]:
        raise HTTPException(422, "folder belongs to a different category")
    db.execute("UPDATE files SET folder_id = ? WHERE id = ?", (fid, file_id))
    if fid:
        return RedirectResponse(f"/folders/{fid}", status_code=303)
    return RedirectResponse(f"/docs?cat={f['category']}", status_code=303)


@router.post("/files/{file_id}/delete")
def file_delete(file_id: int, user=Depends(require_user)):
    f = _get_file(file_id, user)
    db.execute("DELETE FROM files WHERE id = ?", (file_id,))
    path = os.path.join(db.UPLOAD_DIR, f["stored_name"])
    if os.path.isfile(path):
        os.remove(path)
    if f["folder_id"]:
        return RedirectResponse(f"/folders/{f['folder_id']}?msg=File+deleted", status_code=303)
    return RedirectResponse(f"/docs?cat={f['category']}&msg=File+deleted", status_code=303)
