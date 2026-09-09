"""Password hashing (stdlib pbkdf2), DB-backed sessions, login/setup/admin routes."""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db
from .web import page

router = APIRouter()

ITERATIONS = 200_000
SESSION_DAYS = 30
SECURE_COOKIES = os.environ.get("SECURE_COOKIES", "0") == "1"


class RequiresLogin(Exception):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f"pbkdf2:{ITERATIONS}:{salt}:{digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split(":")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters)).hex()
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(calc, digest)


def _now():
    return datetime.now(timezone.utc)


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (_now() + timedelta(days=SESSION_DAYS)).isoformat()
    db.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)", (token, user_id, expires))
    return token


def current_user(request: Request):
    token = request.cookies.get("session")
    if not token:
        return None
    return db.query_one(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ? AND s.expires_at > ? AND u.active = 1""",
        (token, _now().isoformat()),
    )


def require_user(request: Request):
    user = current_user(request)
    if user is None:
        raise RequiresLogin()
    return user


def require_admin(user=Depends(require_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "admin only")
    return user


def _set_session_cookie(response, token):
    response.set_cookie(
        "session", token,
        max_age=SESSION_DAYS * 86400,
        httponly=True, samesite="lax", secure=SECURE_COOKIES,
    )


def _no_users():
    return db.query_one("SELECT COUNT(*) AS n FROM users")["n"] == 0


# ---------- first-run setup ----------

@router.get("/setup")
def setup_form(request: Request):
    if not _no_users():
        return RedirectResponse("/login", status_code=303)
    return page(request, "setup.html")


@router.post("/setup")
def setup(name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    if not _no_users():
        raise HTTPException(403, "setup already completed")
    uid = db.execute(
        "INSERT INTO users (email, name, password_hash, role) VALUES (?, ?, ?, 'admin')",
        (email.strip().lower(), name.strip(), hash_password(password)),
    )
    resp = RedirectResponse("/", status_code=303)
    _set_session_cookie(resp, create_session(uid))
    return resp


# ---------- login / logout ----------

@router.get("/login")
def login_form(request: Request):
    if _no_users():
        return RedirectResponse("/setup", status_code=303)
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return page(request, "login.html", error="")


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = db.query_one("SELECT * FROM users WHERE email = ? AND active = 1", (email.strip().lower(),))
    if not user or not verify_password(password, user["password_hash"]):
        return page(request, "login.html", error="Invalid email or password.")
    nxt = request.query_params.get("next", "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    resp = RedirectResponse(nxt, status_code=303)
    _set_session_cookie(resp, create_session(user["id"]))
    return resp


@router.post("/logout")
def logout(request: Request):
    token = request.cookies.get("session")
    if token:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


# ---------- own profile ----------

@router.get("/profile")
def profile(request: Request, user=Depends(require_user)):
    return page(request, "profile.html", user=user)


@router.post("/profile")
def profile_save(
    user=Depends(require_user),
    name: str = Form(...), title: str = Form(""), phone: str = Form(""),
):
    db.execute("UPDATE users SET name = ?, title = ?, phone = ? WHERE id = ?",
               (name.strip(), title.strip(), phone.strip(), user["id"]))
    return RedirectResponse("/profile?msg=Profile+saved", status_code=303)


@router.post("/profile/password")
def change_password(
    user=Depends(require_user),
    current: str = Form(...), new: str = Form(...),
):
    if not verify_password(current, user["password_hash"]):
        return RedirectResponse("/profile?msg=Current+password+is+wrong", status_code=303)
    if len(new) < 8:
        return RedirectResponse("/profile?msg=New+password+must+be+8%2B+characters", status_code=303)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), user["id"]))
    return RedirectResponse("/profile?msg=Password+changed", status_code=303)


# ---------- admin: user management ----------

@router.get("/admin/users")
def users_page(request: Request, user=Depends(require_admin)):
    users = db.query("SELECT * FROM users ORDER BY active DESC, name")
    return page(request, "users.html", user=user, users=users, new_password=None, new_password_for=None)


@router.post("/admin/users")
def add_user(
    request: Request,
    admin=Depends(require_admin),
    name: str = Form(...), email: str = Form(...),
    title: str = Form(""), role: str = Form("member"),
):
    if role not in ("admin", "member"):
        raise HTTPException(422, "bad role")
    email = email.strip().lower()
    if db.query_one("SELECT id FROM users WHERE email = ?", (email,)):
        return RedirectResponse("/admin/users?msg=That+email+already+exists", status_code=303)
    temp = secrets.token_urlsafe(9)
    db.execute(
        "INSERT INTO users (email, name, title, password_hash, role) VALUES (?, ?, ?, ?, ?)",
        (email, name.strip(), title.strip(), hash_password(temp), role),
    )
    users = db.query("SELECT * FROM users ORDER BY active DESC, name")
    return page(request, "users.html", user=admin, users=users,
                new_password=temp, new_password_for=email)


@router.post("/admin/users/{uid}/reset")
def reset_password(request: Request, uid: int, admin=Depends(require_admin)):
    target = db.query_one("SELECT * FROM users WHERE id = ?", (uid,))
    if not target:
        raise HTTPException(404)
    temp = secrets.token_urlsafe(9)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(temp), uid))
    db.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    users = db.query("SELECT * FROM users ORDER BY active DESC, name")
    return page(request, "users.html", user=admin, users=users,
                new_password=temp, new_password_for=target["email"])


@router.post("/admin/users/{uid}/toggle")
def toggle_user(uid: int, admin=Depends(require_admin)):
    if uid == admin["id"]:
        return RedirectResponse("/admin/users?msg=You+cannot+deactivate+yourself", status_code=303)
    db.execute("UPDATE users SET active = 1 - active WHERE id = ?", (uid,))
    db.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    return RedirectResponse("/admin/users", status_code=303)
