"""Shared Jinja2 environment and page helpers."""
import os

import markdown as _markdown
from fastapi.templating import Jinja2Templates

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL_NAME = os.environ.get("PORTAL_NAME", "Stone Knight")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def render_md(text: str) -> str:
    return _markdown.markdown(text or "", extensions=["tables", "fenced_code", "sane_lists"])


def money(cents) -> str:
    return f"${(cents or 0) / 100:,.2f}"


templates.env.filters["md"] = render_md
templates.env.filters["money"] = money
templates.env.globals["portal_name"] = PORTAL_NAME


def page(request, name, user=None, **ctx):
    ctx.update({"user": user, "msg": request.query_params.get("msg", "")})
    return templates.TemplateResponse(request, name, ctx)
