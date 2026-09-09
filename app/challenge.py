"""Daily cybersecurity challenge: deterministic date-based pick from the bank,
one attempt per user per day, streaks, team leaderboard, and an archive page."""
import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db
from .auth import require_user
from .web import page

router = APIRouter()

KIND_LABELS = {"quiz": "Quiz", "puzzle": "Puzzle", "scenario": "Scenario"}


def _challenge_for(day: date):
    ids = [r["id"] for r in db.query("SELECT id FROM challenges ORDER BY id")]
    if not ids:
        return None
    row = db.query_one("SELECT * FROM challenges WHERE id = ?",
                       (ids[day.toordinal() % len(ids)],))
    ch = dict(row)
    ch["option_list"] = json.loads(ch["options"]) if ch["options"] else []
    ch["kind_label"] = KIND_LABELS[ch["kind"]]
    return ch


def _attempt(user_id: int, day: str):
    return db.query_one(
        """SELECT a.*, c.answer AS correct_answer, c.explanation
           FROM challenge_attempts a JOIN challenges c ON c.id = a.challenge_id
           WHERE a.user_id = ? AND a.day = ?""", (user_id, day))


def _streak(user_id: int) -> int:
    """Consecutive days with a correct answer, ending today (or yesterday if
    today hasn't been played yet — the streak isn't broken until the day ends)."""
    days = {r["day"] for r in db.query(
        "SELECT day FROM challenge_attempts WHERE user_id = ? AND correct = 1", (user_id,))}
    d = date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    streak = 0
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


def widget_ctx(user):
    """Context for the home-page widget; shared with the archive page."""
    today = date.today()
    return {
        "ch_today": _challenge_for(today),
        "ch_attempt": _attempt(user["id"], today.isoformat()),
        "ch_streak": _streak(user["id"]),
    }


def _grade(challenge, raw: str) -> int:
    guess = (raw or "").strip()
    if challenge["option_list"]:
        return 1 if guess == challenge["answer"] else 0
    accepted = [a.strip().lower() for a in challenge["answer"].split("|")]
    return 1 if guess.lower() in accepted else 0


@router.post("/challenge/answer")
def answer(request: Request, user=Depends(require_user),
           guess: str = Form(""), next_url: str = Form("/")):
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    today = date.today()
    challenge = _challenge_for(today)
    if challenge is None:
        raise HTTPException(503, "no challenges loaded")
    if _attempt(user["id"], today.isoformat()):
        return RedirectResponse(next_url, status_code=303)
    if not guess.strip():
        return RedirectResponse(f"{next_url}?msg=Enter+an+answer+first", status_code=303)
    db.execute(
        """INSERT OR IGNORE INTO challenge_attempts (user_id, day, challenge_id, answer, correct)
           VALUES (?, ?, ?, ?, ?)""",
        (user["id"], today.isoformat(), challenge["id"], guess.strip(),
         _grade(challenge, guess)))
    return RedirectResponse(next_url, status_code=303)


@router.get("/challenge")
def archive(request: Request, user=Depends(require_user)):
    users = db.query("SELECT id, name FROM users WHERE active = 1")
    stats = {r["user_id"]: r for r in db.query(
        """SELECT user_id, COUNT(*) AS attempts, SUM(correct) AS correct
           FROM challenge_attempts GROUP BY user_id""")}
    board = [{"name": u["name"],
              "attempts": stats[u["id"]]["attempts"],
              "correct": stats[u["id"]]["correct"] or 0,
              "streak": _streak(u["id"])}
             for u in users if u["id"] in stats]
    board.sort(key=lambda r: (-r["correct"], -r["streak"], r["name"]))

    history = []
    today = date.today()
    for offset in range(1, 15):
        day = today - timedelta(days=offset)
        attempt = _attempt(user["id"], day.isoformat())
        if attempt:
            row = db.query_one("SELECT * FROM challenges WHERE id = ?", (attempt["challenge_id"],))
            challenge = dict(row)
            challenge["option_list"] = json.loads(challenge["options"]) if challenge["options"] else []
            challenge["kind_label"] = KIND_LABELS[challenge["kind"]]
        else:
            challenge = _challenge_for(day)
        if challenge:
            history.append({"day": day.isoformat(), "challenge": challenge, "attempt": attempt})

    return page(request, "challenge.html", user=user, board=board, history=history,
                next_url="/challenge", **widget_ctx(user))
