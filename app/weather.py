"""Weather widget backed by Open-Meteo (free, no API key). Results cached 10 min."""
import asyncio
import time

import httpx
from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse

from . import db
from .auth import require_user

router = APIRouter()

CACHE_TTL = 600
_cache: dict[int, tuple[float, dict]] = {}

# WMO weather codes → (emoji, description)
WMO = {
    0: ("☀️", "Clear"), 1: ("🌤️", "Mostly clear"), 2: ("⛅", "Partly cloudy"), 3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"), 48: ("🌫️", "Rime fog"),
    51: ("🌦️", "Light drizzle"), 53: ("🌦️", "Drizzle"), 55: ("🌧️", "Heavy drizzle"),
    56: ("🌧️", "Freezing drizzle"), 57: ("🌧️", "Freezing drizzle"),
    61: ("🌧️", "Light rain"), 63: ("🌧️", "Rain"), 65: ("🌧️", "Heavy rain"),
    66: ("🌧️", "Freezing rain"), 67: ("🌧️", "Freezing rain"),
    71: ("🌨️", "Light snow"), 73: ("🌨️", "Snow"), 75: ("❄️", "Heavy snow"), 77: ("❄️", "Snow grains"),
    80: ("🌦️", "Showers"), 81: ("🌧️", "Showers"), 82: ("⛈️", "Heavy showers"),
    85: ("🌨️", "Snow showers"), 86: ("🌨️", "Snow showers"),
    95: ("⛈️", "Thunderstorm"), 96: ("⛈️", "Thunderstorm with hail"), 99: ("⛈️", "Thunderstorm with hail"),
}


async def _fetch_city(client: httpx.AsyncClient, city) -> dict:
    cached = _cache.get(city["id"])
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    r = await client.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": city["lat"], "longitude": city["lon"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        })
    r.raise_for_status()
    cur = r.json()["current"]
    emoji, desc = WMO.get(cur["weather_code"], ("🌡️", "—"))
    payload = {
        "id": city["id"], "name": city["name"], "emoji": emoji, "desc": desc,
        "temp": cur["temperature_2m"], "feels": cur["apparent_temperature"],
        "humidity": cur["relative_humidity_2m"], "wind": cur["wind_speed_10m"],
    }
    _cache[city["id"]] = (time.time(), payload)
    return payload


@router.get("/api/weather")
async def weather(user=Depends(require_user)):
    cities = db.query("SELECT * FROM weather_cities ORDER BY id")
    async with httpx.AsyncClient(timeout=8) as client:
        results = await asyncio.gather(*(_fetch_city(client, c) for c in cities),
                                       return_exceptions=True)
    ok = [r for r in results if isinstance(r, dict)]
    return {"cities": ok, "errors": len(results) - len(ok)}


@router.post("/weather/cities")
async def add_city(city: str = Form(...), user=Depends(require_user)):
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            r = await client.get("https://geocoding-api.open-meteo.com/v1/search",
                                 params={"name": city.strip(), "count": 1})
            r.raise_for_status()
            results = r.json().get("results") or []
        except httpx.HTTPError:
            return RedirectResponse("/?msg=Weather+service+unreachable", status_code=303)
    if not results:
        return RedirectResponse(f"/?msg=City+not+found:+{city.strip()}", status_code=303)
    hit = results[0]
    region = ", ".join(x for x in (hit.get("admin1"), hit.get("country_code")) if x)
    db.execute("INSERT INTO weather_cities (name, region, lat, lon) VALUES (?, ?, ?, ?)",
               (hit["name"], region, hit["latitude"], hit["longitude"]))
    return RedirectResponse("/", status_code=303)


@router.post("/weather/cities/{city_id}/delete")
def remove_city(city_id: int, user=Depends(require_user)):
    db.execute("DELETE FROM weather_cities WHERE id = ?", (city_id,))
    _cache.pop(city_id, None)
    return RedirectResponse("/", status_code=303)
