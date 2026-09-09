"""Kanban task board: todo / doing / done."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db
from .auth import require_user
from .web import page

router = APIRouter()

STATUSES = ["todo", "doing", "done"]


@router.get("/tasks")
def board(request: Request, user=Depends(require_user)):
    tasks = db.query(
        """SELECT t.*, u.name AS assignee FROM tasks t
           LEFT JOIN users u ON u.id = t.assignee_id ORDER BY t.updated_at DESC""")
    columns = {s: [t for t in tasks if t["status"] == s] for s in STATUSES}
    members = db.query("SELECT id, name FROM users WHERE active = 1 ORDER BY name")
    return page(request, "tasks.html", user=user, columns=columns, members=members)


@router.post("/tasks")
def add_task(title: str = Form(...), note: str = Form(""), assignee_id: str = Form(""),
             user=Depends(require_user)):
    assignee = int(assignee_id) if assignee_id else None
    db.execute("INSERT INTO tasks (title, note, assignee_id) VALUES (?, ?, ?)",
               (title.strip(), note.strip(), assignee))
    return RedirectResponse("/tasks", status_code=303)


def _get_task(task_id: int):
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise HTTPException(404, "task not found")
    return task


@router.post("/tasks/{task_id}/move")
def move_task(task_id: int, direction: str = Form(...), user=Depends(require_user)):
    task = _get_task(task_id)
    i = STATUSES.index(task["status"]) + (1 if direction == "next" else -1)
    if 0 <= i < len(STATUSES):
        db.execute("UPDATE tasks SET status = ?, updated_at = datetime('now') WHERE id = ?",
                   (STATUSES[i], task_id))
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/assign")
def assign_task(task_id: int, assignee_id: str = Form(""), user=Depends(require_user)):
    _get_task(task_id)
    assignee = int(assignee_id) if assignee_id else None
    db.execute("UPDATE tasks SET assignee_id = ?, updated_at = datetime('now') WHERE id = ?",
               (assignee, task_id))
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/delete")
def delete_task(task_id: int, user=Depends(require_user)):
    _get_task(task_id)
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return RedirectResponse("/tasks", status_code=303)
