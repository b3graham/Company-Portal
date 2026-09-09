"""World news widget: top stories from the BBC World RSS feed, cached 15 min."""
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import httpx
from fastapi import APIRouter, Depends

from .auth import require_user

router = APIRouter()

FEED_URL = "https://feeds.bbci.co.uk/news/world/rss.xml"
SOURCE = "BBC World News"
TOP_N = 5
CACHE_TTL = 900
_cache: dict = {"ts": 0.0, "items": []}


@router.get("/api/news")
async def news(user=Depends(require_user)):
    if time.time() - _cache["ts"] < CACHE_TTL and _cache["items"]:
        return {"items": _cache["items"], "source": SOURCE}
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.get(FEED_URL)
            r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for item in root.iter("item"):
            entry = {"title": item.findtext("title") or "", "link": item.findtext("link") or ""}
            try:
                entry["ts"] = parsedate_to_datetime(item.findtext("pubDate")).timestamp()
            except (TypeError, ValueError):
                entry["ts"] = None
            items.append(entry)
            if len(items) == TOP_N:
                break
        if items:
            _cache.update(ts=time.time(), items=items)
        return {"items": items, "source": SOURCE}
    except (httpx.HTTPError, ET.ParseError):
        # Serve stale items if we have them; the widget shows a notice otherwise.
        return {"items": _cache["items"], "source": SOURCE, "stale": True}
