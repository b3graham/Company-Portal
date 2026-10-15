import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import announce, auth, challenge, crm, db, directory, docs, expenses, importer, it, it_docs, it_ops, links, news, stocks, tasks, weather
from .auth import RequiresLogin
from .web import PORTAL_NAME

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    yield


# docs_url=None frees up /docs for our documents page (and hides the API schema).
app = FastAPI(title=PORTAL_NAME, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.exception_handler(RequiresLogin)
async def login_redirect(request: Request, _exc: RequiresLogin):
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


app.include_router(auth.router)
app.include_router(announce.router)
app.include_router(docs.router)
app.include_router(tasks.router)
app.include_router(challenge.router)
app.include_router(crm.router)
app.include_router(directory.router)
app.include_router(expenses.router)
app.include_router(importer.router)
app.include_router(it.router)
app.include_router(it_docs.router)
app.include_router(it_ops.router)
app.include_router(links.router)
app.include_router(weather.router)
app.include_router(news.router)
app.include_router(stocks.router)
