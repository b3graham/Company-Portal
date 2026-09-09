"""Quick links dashboard: a company start page of tools and resources."""
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db
from .auth import require_user
from .web import page

router = APIRouter()


@router.get("/links")
def links(request: Request, user=Depends(require_user)):
    rows = db.query("SELECT * FROM links ORDER BY category, title")
    groups: dict[str, list] = {}
    for r in rows:
        link = dict(r)
        link["host"] = urlparse(r["url"]).netloc
        groups.setdefault(r["category"], []).append(link)
    return page(request, "links.html", user=user, groups=groups)


@router.post("/links")
def add_link(title: str = Form(...), url: str = Form(...), note: str = Form(""),
             category: str = Form("General"), user=Depends(require_user)):
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    db.execute("INSERT INTO links (title, url, note, category) VALUES (?, ?, ?, ?)",
               (title.strip(), url, note.strip(), category.strip() or "General"))
    return RedirectResponse("/links", status_code=303)


@router.post("/links/{link_id}/delete")
def delete_link(link_id: int, user=Depends(require_user)):
    if not db.query_one("SELECT id FROM links WHERE id = ?", (link_id,)):
        raise HTTPException(404)
    db.execute("DELETE FROM links WHERE id = ?", (link_id,))
    return RedirectResponse("/links", status_code=303)
