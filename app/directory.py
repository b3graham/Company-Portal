"""Team page: employee directory + onboarding checklist."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db
from .auth import require_admin, require_user
from .web import page

router = APIRouter()


@router.get("/team")
def team(request: Request, user=Depends(require_user)):
    members = db.query("SELECT * FROM users WHERE active = 1 ORDER BY name")
    items = db.query("SELECT * FROM onboarding_items ORDER BY position, id")
    done = {r["item_id"] for r in db.query(
        "SELECT item_id FROM onboarding_done WHERE user_id = ?", (user["id"],))}
    return page(request, "team.html", user=user, members=members, items=items, done=done)


@router.post("/onboarding/{item_id}/toggle")
def toggle_item(item_id: int, user=Depends(require_user)):
    if not db.query_one("SELECT id FROM onboarding_items WHERE id = ?", (item_id,)):
        raise HTTPException(404)
    if db.query_one("SELECT 1 FROM onboarding_done WHERE user_id = ? AND item_id = ?",
                    (user["id"], item_id)):
        db.execute("DELETE FROM onboarding_done WHERE user_id = ? AND item_id = ?",
                   (user["id"], item_id))
    else:
        db.execute("INSERT INTO onboarding_done (user_id, item_id) VALUES (?, ?)",
                   (user["id"], item_id))
    return RedirectResponse("/team", status_code=303)


@router.post("/admin/onboarding")
def add_item(label: str = Form(...), detail: str = Form(""), admin=Depends(require_admin)):
    pos = db.query_one("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM onboarding_items")["p"]
    db.execute("INSERT INTO onboarding_items (label, detail, position) VALUES (?, ?, ?)",
               (label.strip(), detail.strip(), pos))
    return RedirectResponse("/team", status_code=303)


@router.post("/admin/onboarding/{item_id}/delete")
def delete_item(item_id: int, admin=Depends(require_admin)):
    db.execute("DELETE FROM onboarding_items WHERE id = ?", (item_id,))
    return RedirectResponse("/team", status_code=303)
