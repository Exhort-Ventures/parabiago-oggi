#!/usr/bin/env python3
"""Refresh verified events for Parabiago Oggi.

The collector combines source-specific municipal listing extraction with a small
verified bootstrap layer. Bootstrap entries are only retained while their dates
remain inside the rolling 30-day window and always link to an official source.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sources.json"
OUTPUT = ROOT / "data" / "events.json"
CACHE = ROOT / "data" / "geocode-cache.json"
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "ParabiagoOggi/1.1 (+https://github.com/Exhort-Ventures/parabiago-oggi)",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.6",
})

MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9,
    "ottobre": 10, "novembre": 11, "dicembre": 12,
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6,
    "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12,
}
CATEGORY_RULES = {
    "nightlife": ["serata", "danza", "dj", "aperitivo", "night"],
    "music": ["musica", "concerto", "jazz", "live", "festival"],
    "cinema": ["cinema", "film", "proiezione"],
    "culture": ["mostra", "museo", "teatro", "spettacolo", "libro"],
    "sport": ["sport", "corsa", "torneo", "fitness"],
    "food": ["food", "sagra", "mercato", "degustazione"],
    "community": ["festa", "fiera", "comunità", "quartiere"],
}
CATEGORY_LABELS = {
    "nightlife": "Vita notturna", "music": "Musica", "cinema": "Cinema",
    "culture": "Cultura", "sport": "Sport", "food": "Food & mercati",
    "community": "Comunità", "other": "Altro",
}


def fetch(url: str) -> str:
    response = SESSION.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def parse_datetime(value: str) -> datetime | None:
    try:
        dt = date_parser.parse(value, dayfirst=True)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone(timedelta(hours=2)))
    except (ValueError, TypeError, OverflowError):
        return None


def italian_date(text: str) -> datetime | None:
    clean = re.sub(r"\s+", " ", text.lower())
    match = re.search(r"(\d{1,2})\s+([a-zà]+)(?:\s+(\d{4}))?", clean)
    if not match:
        return None
    month = MONTHS.get(match.group(2).strip("."))
    if not month:
        return None
    year = int(match.group(3) or datetime.now().year)
    hour_match = re.search(r"(?:ore|dalle)\s*(\d{1,2})[.:](\d{2})", clean)
    hour = int(hour_match.group(1)) if hour_match else 18
    minute = int(hour_match.group(2)) if hour_match else 0
    try:
        return datetime(year, month, int(match.group(1)), hour, minute, tzinfo=timezone(timedelta(hours=2)))
    except ValueError:
        return None


def category_for(text: str) -> str:
    lower = text.lower()
    scores = {name: sum(term in lower for term in terms) for name, terms in CATEGORY_RULES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "other"


def event_from_block(block, source: dict) -> dict | None:
    heading = block.find(["h2", "h3", "h4"])
    if not heading:
        return None
    title = heading.get_text(" ", strip=True)
    if not title or title.lower() in {"eventi", "esplora tutti gli eventi", "in evidenza"}:
        return None
    text = block.get_text(" ", strip=True)
    start = italian_date(text)
    if not start:
        return None
    anchor = heading.find("a", href=True) or block.find("a", href=True)
    source_url = urljoin(source["url"], anchor["href"]) if anchor else source["url"]
    venue_match = re.search(
        r"(?:presso|in|a)\s+((?:piazza|via|viale|corso|parco|biblioteca|auditorium|teatro|centro|chiostro)[^.;]{2,100})",
        text, re.I,
    )
    venue = venue_match.group(1).strip() if venue_match else f"Centro di {source['city']}"
    free = bool(re.search(r"gratuit|ingresso libero", text, re.I))
    category = category_for(f"{title} {text}")
    return {
        "title": title,
        "start": start.isoformat(),
        "venue": venue,
        "city": source["city"],
        "description": text[:650],
        "free": free,
        "price": "Gratis" if free else "Verificare sulla fonte ufficiale",
        "booking": "Verificare sulla fonte ufficiale",
        "rules": "Verificare eventuali variazioni sulla fonte ufficiale.",
        "transport": "Apri Google Maps per il percorso.",
        "sourceName": source["name"],
        "sourceUrl": source_url,
        "category": category,
        "categoryLabel": CATEGORY_LABELS[category],
    }


def parse_municipal_listing(source: dict) -> list[dict]:
    soup = BeautifulSoup(fetch(source["url"]), "html.parser")
    events: list[dict] = []
    seen_titles: set[str] = set()
    for heading in soup.find_all(["h2", "h3", "h4"]):
        block = heading.find_parent(["article", "li", "div", "section"]) or heading.parent
        event = event_from_block(block, source)
        if event and event["title"].lower() not in seen_titles:
            seen_titles.add(event["title"].lower())
            events.append(event)
    return events


def load_cache() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def geocode(query: str, cache: dict) -> tuple[float, float] | None:
    key = re.sub(r"\s+", " ", query.lower()).strip()
    if key in cache:
        item = cache[key]
        return (item["lat"], item["lng"]) if item else None
    response = SESSION.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "it"},
        timeout=30,
    )
    response.raise_for_status()
    results = response.json()
    coords = (float(results[0]["lat"]), float(results[0]["lon"])) if results else None
    cache[key] = {"lat": coords[0], "lng": coords[1]} if coords else None
    time.sleep(1.1)
    return coords


def distance_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def event_id(event: dict) -> str:
    raw = f"{event['title'].lower()}|{event['start'][:16]}|{event['city'].lower()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:14]


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=config["horizonDays"])
    candidates = list(config.get("verifiedBootstrapEvents", []))
    failures: list[dict] = []
    source_counts: dict[str, int] = {}

    for source in config["sources"]:
        try:
            found = parse_municipal_listing(source)
            candidates.extend(found)
            source_counts[source["name"]] = len(found)
        except Exception as exc:
            failures.append({"source": source["name"], "error": str(exc)[:250]})
            source_counts[source["name"]] = 0

    cache = load_cache()
    accepted: dict[str, dict] = {}
    for event in candidates:
        start = parse_datetime(event.get("start", ""))
        if not start:
            continue
        start_utc = start.astimezone(timezone.utc)
        end = parse_datetime(event.get("end", "")) if event.get("end") else None
        effective_end = end.astimezone(timezone.utc) if end else start_utc
        if effective_end < now - timedelta(hours=6) or start_utc > horizon:
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
        accepted[event["id"]] = event

    events = sorted(accepted.values(), key=lambda item: item["start"])
    minimum = int(config.get("minimumExpectedEvents", 1))
    if len(events) < minimum:
        raise RuntimeError(
            f"Extraction guard: expected at least {minimum} event(s), produced {len(events)}. "
            f"Source counts: {source_counts}; failures: {failures}"
        )

    payload = {
        "updatedAt": now.isoformat(), "mode": "live", "radiusKm": config["radiusKm"],
        "horizonDays": config["horizonDays"], "eventCount": len(events), "events": events,
        "sourceHealth": {"sourceCounts": source_counts, "failures": failures[:25]},
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Published {len(events)} verified events. Source counts: {source_counts}")


if __name__ == "__main__":
    main()
