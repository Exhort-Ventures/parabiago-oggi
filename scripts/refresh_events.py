#!/usr/bin/env python3
"""Collect verified public events for Parabiago Oggi.

The collector is deliberately conservative: an event is published only when it has
an identifiable title, future date, source URL and a geocodable venue within 20 km.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sources.json"
OUTPUT = ROOT / "data" / "events.json"
CACHE = ROOT / "data" / "geocode-cache.json"
HEADERS = {
    "User-Agent": "ParabiagoOggi/1.0 (+https://github.com/Exhort-Ventures/parabiago-oggi)",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.6",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

CATEGORY_RULES = {
    "nightlife": ["dj", "discoteca", "club", "night", "aperitivo", "serata"],
    "music": ["musica", "concerto", "jazz", "live", "festival"],
    "cinema": ["cinema", "film", "proiezione"],
    "culture": ["mostra", "museo", "teatro", "spettacolo", "visita", "libro"],
    "sport": ["sport", "corsa", "torneo", "bicicletta", "fitness"],
    "food": ["food", "cibo", "sagra", "mercato", "street food", "degustazione"],
    "community": ["festa", "comunità", "volontariato", "quartiere"],
}
CATEGORY_LABELS = {
    "nightlife": "Vita notturna", "music": "Musica", "cinema": "Cinema",
    "culture": "Cultura", "sport": "Sport", "food": "Food & mercati",
    "community": "Comunità", "other": "Altro",
}


def fetch(url: str) -> str:
    response = SESSION.get(url, timeout=25)
    response.raise_for_status()
    return response.text


def flatten_jsonld(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from flatten_jsonld(item)
    elif isinstance(value, dict):
        if "@graph" in value:
            yield from flatten_jsonld(value["@graph"])
        else:
            yield value


def discover_links(source: dict[str, Any]) -> list[str]:
    soup = BeautifulSoup(fetch(source["url"]), "html.parser")
    links: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = urljoin(source["url"], anchor.get("href", ""))
        parsed = urlparse(href)
        if parsed.hostname not in source["allowedHosts"]:
            continue
        haystack = f"{href} {anchor.get_text(' ', strip=True)}".lower()
        if any(token in haystack for token in ("evento", "eventi/", "letturaevento", "vivere-il-comune/eventi")):
            links.add(href.split("#")[0])
    return sorted(links)[:100]


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = date_parser.parse(str(value), dayfirst=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=2)))
        return dt
    except (ValueError, TypeError, OverflowError):
        return None


def address_from_location(location: Any, default_city: str) -> tuple[str, str]:
    if isinstance(location, str):
        return location.strip(), default_city
    if not isinstance(location, dict):
        return "", default_city
    name = str(location.get("name") or "").strip()
    address = location.get("address")
    if isinstance(address, str):
        return " · ".join(part for part in (name, address.strip()) if part), default_city
    if isinstance(address, dict):
        street = str(address.get("streetAddress") or "").strip()
        city = str(address.get("addressLocality") or default_city).strip()
        venue = " · ".join(part for part in (name, street) if part)
        return venue, city
    return name, default_city


def category_for(text: str) -> str:
    lower = text.lower()
    scores = {category: sum(keyword in lower for keyword in keywords) for category, keywords in CATEGORY_RULES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "other"


def text_fallback(soup: BeautifulSoup, url: str, source: dict[str, Any]) -> dict[str, Any] | None:
    title_node = soup.select_one("h1")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    text = soup.get_text(" ", strip=True)
    if not title or len(text) < 80:
        return None

    patterns = [
        r"(?:dal\s+)?(\d{1,2}\s+[a-zà]+\s+2026)(?:\s+al\s+\d{1,2}\s+[a-zà]+\s+2026)?(?:\s+(?:dalle|ore)\s+(\d{1,2}[.:]\d{2}))?",
        r"(\d{1,2}[/-]\d{1,2}[/-]2026)(?:\s+(?:dalle|ore)\s+(\d{1,2}[.:]\d{2}))?",
    ]
    start = None
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            value = match.group(1)
            if match.lastindex and match.lastindex >= 2 and match.group(2):
                value += " " + match.group(2).replace(".", ":")
            start = parse_datetime(value)
            break
    if not start:
        return None

    address_match = re.search(r"(?:Indirizzo|Dove|presso)\s*[:\-]?\s*([^|]{5,100}?(?:MI|Milano|Parabiago|Nerviano|Legnano|Rho))", text, flags=re.I)
    venue = address_match.group(1).strip(" .,-") if address_match else ""
    if not venue:
        return None

    free = bool(re.search(r"\b(gratuito|gratuita|ingresso libero)\b", text, flags=re.I))
    description = text[:700]
    category = category_for(f"{title} {description}")
    return {
        "title": title, "start": start.isoformat(), "venue": venue,
        "city": source["city"], "description": description, "free": free,
        "price": "Gratis" if free else "Verificare presso l'organizzatore",
        "booking": "Verificare sulla fonte ufficiale", "rules": "Verificare eventuali variazioni sulla fonte ufficiale.",
        "transport": "Apri Google Maps per il percorso.", "sourceName": source["name"],
        "sourceUrl": url, "category": category, "categoryLabel": CATEGORY_LABELS[category],
    }


def parse_event_page(url: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    soup = BeautifulSoup(fetch(url), "html.parser")
    events: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "null")
        except json.JSONDecodeError:
            continue
        for item in flatten_jsonld(payload):
            item_type = item.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if "Event" not in types:
                continue
            start = parse_datetime(item.get("startDate"))
            title = str(item.get("name") or "").strip()
            venue, city = address_from_location(item.get("location"), source["city"])
            if not start or not title or not venue:
                continue
            description = BeautifulSoup(str(item.get("description") or ""), "html.parser").get_text(" ", strip=True)
            offers = item.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price_value = offers.get("price") if isinstance(offers, dict) else None
            free = str(price_value).strip() in {"0", "0.0", "0,00"} or "gratuit" in description.lower()
            category = category_for(f"{title} {description}")
            events.append({
                "title": title, "start": start.isoformat(), "venue": venue, "city": city,
                "description": description[:700] or "Consulta la fonte ufficiale per i dettagli.",
                "free": free, "price": "Gratis" if free else (f"€{price_value}" if price_value else "Verificare presso l'organizzatore"),
                "booking": "Verificare sulla fonte ufficiale", "rules": "Verificare eventuali variazioni sulla fonte ufficiale.",
                "transport": "Apri Google Maps per il percorso.", "sourceName": source["name"],
                "sourceUrl": str(item.get("url") or url), "category": category,
                "categoryLabel": CATEGORY_LABELS[category],
            })
    if not events:
        fallback = text_fallback(soup, url, source)
        if fallback:
            events.append(fallback)
    return events


def load_cache() -> dict[str, Any]:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def geocode(query: str, cache: dict[str, Any]) -> tuple[float, float] | None:
    key = re.sub(r"\s+", " ", query.lower()).strip()
    if key in cache:
        value = cache[key]
        return (value["lat"], value["lng"]) if value else None
    response = SESSION.get("https://nominatim.openstreetmap.org/search", params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "it"}, timeout=25)
    response.raise_for_status()
    payload = response.json()
    result = (float(payload[0]["lat"]), float(payload[0]["lon"])) if payload else None
    cache[key] = {"lat": result[0], "lng": result[1]} if result else None
    time.sleep(1.1)
    return result


def distance_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def event_id(event: dict[str, Any]) -> str:
    raw = f"{event['title'].lower()}|{event['start'][:16]}|{event['city'].lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=config["horizonDays"])
    cache = load_cache()
    candidates: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for source in config["sources"]:
        try:
            links = discover_links(source)
        except Exception as exc:  # source isolation is intentional
            failures.append({"source": source["name"], "error": str(exc)[:200]})
            continue
        for link in links:
            try:
                candidates.extend(parse_event_page(link, source))
            except Exception as exc:
                failures.append({"source": source["name"], "url": link, "error": str(exc)[:200]})

    accepted: dict[str, dict[str, Any]] = {}
    for event in candidates:
        start = parse_datetime(event["start"])
        if not start:
            continue
        start_utc = start.astimezone(timezone.utc)
        if start_utc < now - timedelta(hours=6) or start_utc > horizon:
            continue
        coords = geocode(f"{event['venue']}, {event['city']}, Lombardia, Italia", cache)
        if not coords:
            continue
        distance = distance_km(config["centre"]["lat"], config["centre"]["lng"], *coords)
        if distance > config["radiusKm"]:
            continue
        event.update({
            "id": event_id(event), "lat": coords[0], "lng": coords[1],
            "distanceKm": round(distance, 1), "verifiedAt": now.date().isoformat(),
            "verificationStatus": "Fonte ufficiale",
        })
        current = accepted.get(event["id"])
        if not current or len(event["description"]) > len(current["description"]):
            accepted[event["id"]] = event

    events = sorted(accepted.values(), key=lambda item: item["start"])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "updatedAt": now.isoformat(), "mode": "live", "radiusKm": config["radiusKm"],
        "horizonDays": config["horizonDays"], "eventCount": len(events),
        "events": events, "sourceHealth": {"failures": failures[:25]},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Published {len(events)} verified events; {len(failures)} source/page failures")


if __name__ == "__main__":
    main()
