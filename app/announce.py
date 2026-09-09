"""Home page: announcements / news feed."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import challenge, db
from .auth import require_user
from .web import page

router = APIRouter()


@router.get("/")
def home(request: Request, user=Depends(require_user)):
    posts = db.query(
        """SELECT a.*, u.name AS author FROM announcements a
           LEFT JOIN users u ON u.id = a.author_id ORDER BY a.id DESC LIMIT 50""")
    open_tasks = db.query_one("SELECT COUNT(*) AS n FROM tasks WHERE status != 'done'")["n"]
    cities = db.query("SELECT * FROM weather_cities ORDER BY id")
    stocks = db.query("SELECT * FROM ticker_symbols ORDER BY id")
    return page(request, "home.html", user=user, posts=posts, open_tasks=open_tasks,
                cities=cities, stocks=stocks, **challenge.widget_ctx(user))


@router.post("/announcements")
def post_announcement(title: str = Form(...), body: str = Form(""), user=Depends(require_user)):
    db.execute("INSERT INTO announcements (title, body, author_id) VALUES (?, ?, ?)",
               (title.strip(), body, user["id"]))
    return RedirectResponse("/", status_code=303)


@router.post("/announcements/{post_id}/delete")
def delete_announcement(post_id: int, user=Depends(require_user)):
    post = db.query_one("SELECT * FROM announcements WHERE id = ?", (post_id,))
    if not post:
        raise HTTPException(404)
    if post["author_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403, "only the author or an admin can delete this")
    db.execute("DELETE FROM announcements WHERE id = ?", (post_id,))
    return RedirectResponse("/", status_code=303)
