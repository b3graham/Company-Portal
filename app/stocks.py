"""Stock ticker widget backed by Yahoo's public chart API (no key). Quotes cached 2 min."""
import asyncio
import time

import httpx
from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse

from . import db
from .auth import require_user

router = APIRouter()

CACHE_TTL = 120
_cache: dict[str, tuple[float, dict]] = {}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"


async def _fetch_quote(client: httpx.AsyncClient, row) -> dict:
    symbol = row["symbol"]
    cached = _cache.get(symbol)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    r = await client.get(CHART_URL.format(symbol=symbol),
                         params={"range": "1d", "interval": "1d"}, headers=HEADERS)
    r.raise_for_status()
    result = (r.json().get("chart") or {}).get("result")
    if not result:
        raise ValueError(f"{symbol}: no data")
    meta = result[0]["meta"]
    price = meta.get("regularMarketPrice")
    # With range=1d the chart starts at today's session, so chartPreviousClose
    # is the prior session's close — exactly what the daily change needs.
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None:
        raise ValueError(f"{symbol}: no price")
    payload = {
        "id": row["id"], "symbol": symbol,
        "name": row["name"] or meta.get("shortName") or symbol,
        "price": price,
        "change": price - prev if prev else None,
        "change_pct": (price / prev - 1) * 100 if prev else None,
    }
    _cache[symbol] = (time.time(), payload)
    return payload


@router.get("/api/stocks")
async def stocks(user=Depends(require_user)):
    rows = db.query("SELECT * FROM ticker_symbols ORDER BY id")
    async with httpx.AsyncClient(timeout=10) as client:
        results = await asyncio.gather(*(_fetch_quote(client, r) for r in rows),
                                       return_exceptions=True)
    ok = []
    for row, res in zip(rows, results):
        if isinstance(res, dict):
            ok.append(res)
        else:
            # Serve the last cached quote (however old) rather than dropping the symbol.
            cached = _cache.get(row["symbol"])
            if cached:
                ok.append(cached[1])
    return {"stocks": ok, "errors": len(rows) - len(ok)}


@router.post("/stocks")
async def add_symbol(query: str = Form(...), user=Depends(require_user)):
    q = query.strip()
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(SEARCH_URL, headers=HEADERS,
                                 params={"q": q, "quotesCount": 5, "newsCount": 0, "listsCount": 0})
            r.raise_for_status()
            quotes = [x for x in r.json().get("quotes", []) if x.get("symbol")]
        except httpx.HTTPError:
            return RedirectResponse("/?msg=Quote+service+unreachable", status_code=303)
    # Prefer an exact symbol match (so typing "A" adds A, not whatever ranks first).
    hit = next((x for x in quotes if x["symbol"].upper() == q.upper()), quotes[0] if quotes else None)
    if not hit:
        return RedirectResponse(f"/?msg=No+match+for:+{q}", status_code=303)
    name = hit.get("shortname") or hit.get("longname") or hit["symbol"]
    if db.query_one("SELECT 1 FROM ticker_symbols WHERE symbol = ?", (hit["symbol"],)):
        return RedirectResponse(f"/?msg=Already+on+the+ticker:+{hit['symbol']}", status_code=303)
    db.execute("INSERT INTO ticker_symbols (symbol, name) VALUES (?, ?)", (hit["symbol"], name))
    return RedirectResponse("/", status_code=303)


@router.post("/stocks/{symbol_id}/delete")
def remove_symbol(symbol_id: int, user=Depends(require_user)):
    row = db.query_one("SELECT * FROM ticker_symbols WHERE id = ?", (symbol_id,))
    if row:
        db.execute("DELETE FROM ticker_symbols WHERE id = ?", (symbol_id,))
        _cache.pop(row["symbol"], None)
    return RedirectResponse("/", status_code=303)
