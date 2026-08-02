#!/usr/bin/env python3
"""Discover and normalise events around Parabiago.

Discovery is intentionally broad. Source confidence is exposed to users, while
geographic and date validation remain strict. A degraded refresh never replaces
the last known-good dataset.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
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
    "User-Agent": "ParabiagoOggi/2.0 (+https://github.com/Exhort-Ventures/parabiago-oggi)",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.6",
})

MONTHS = {"gennaio":1,"febbraio":2,"marzo":3,"aprile":4,"maggio":5,"giugno":6,"luglio":7,"agosto":8,"settembre":9,"ottobre":10,"novembre":11,"dicembre":12,"gen":1,"feb":2,"mar":3,"apr":4,"mag":5,"giu":6,"lug":7,"ago":8,"set":9,"ott":10,"nov":11,"dic":12}
CATEGORY_RULES = {
    "nightlife": ["dj", "discoteca", "club", "night", "aperitivo", "serata danzante", "reggaeton"],
    "music": ["concerto", "live", "jazz", "musica", "tribute", "festival"],
    "cinema": ["cinema", "film", "proiezione"],
    "culture": ["mostra", "museo", "teatro", "spettacolo", "visita", "libro"],
    "sport": ["sport", "corsa", "torneo", "fitness", "bicicletta"],
    "food": ["sagra", "street food", "degustazione", "mercato", "birra", "cibo"],
    "community": ["festa", "fiera", "notte bianca", "quartiere"],
}
CATEGORY_LABELS = {"nightlife":"Vita notturna","music":"Musica","cinema":"Cinema","culture":"Cultura","sport":"Sport","food":"Food & mercati","community":"Comunità","other":"Altro"}


def fetch(url: str) -> str:
    response = SESSION.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def parse_datetime(value: str) -> datetime | None:
    try:
        dt = date_parser.parse(value, dayfirst=True)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone(timedelta(hours=2)))
    except (ValueError, TypeError, OverflowError):
        return None


def italian_date(text: str) -> datetime | None:
    text = clean(text).lower()
    match = re.search(r"(\d{1,2})\s+([a-zà]+)(?:\s+(\d{4}))?", text)
    if not match or match.group(2).strip(".") not in MONTHS:
        return None
    month = MONTHS[match.group(2).strip(".")]
    year = int(match.group(3) or datetime.now().year)
    clock = re.search(r"(?:ore|dalle|alle)?\s*(\d{1,2})[.:](\d{2})", text)
    hour, minute = (int(clock.group(1)), int(clock.group(2))) if clock else (18, 0)
    try:
        return datetime(year, month, int(match.group(1)), hour, minute, tzinfo=timezone(timedelta(hours=2)))
    except ValueError:
        return None


def category_for(text: str) -> str:
    lower = text.lower()
    scores = {key: sum(term in lower for term in terms) for key, terms in CATEGORY_RULES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "other"


def candidate(title: str, start: datetime, venue: str, city: str, description: str, source: dict, source_url: str, free: bool = False) -> dict:
    category = category_for(f"{title} {description}")
    return {
        "title": clean(title), "start": start.isoformat(), "venue": clean(venue), "city": clean(city),
        "description": clean(description)[:700], "free": free,
        "price": "Gratis" if free else "Verificare sulla fonte",
        "booking": "Verificare sulla fonte", "rules": "Verificare eventuali variazioni prima di partire.",
        "transport": f"{clean(venue)}, {clean(city)}.", "sourceName": source["name"],
        "sourceUrl": source_url, "sourceType": source["type"], "confidence": source["confidence"],
        "category": category, "categoryLabel": CATEGORY_LABELS[category],
    }


def parse_cheventi(source: dict) -> list[dict]:
    soup = BeautifulSoup(fetch(source["url"]), "html.parser")
    events = []
    for anchor in soup.select('a[href*="/eventi/"]'):
        block = anchor.find_parent(["article", "li", "div"]) or anchor.parent
        text = clean(block.get_text(" ", strip=True))
        title = clean(anchor.get_text(" ", strip=True))
        if len(title) < 5 or len(text) < 20:
            continue
        start = italian_date(text)
        place = re.search(r"\ba\s+([A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÿ' -]{2,40})(?:\s+(?:da|sabato|domenica|lunedì|martedì|mercoledì|giovedì|venerdì)|\s+\d)", text)
        if not start or not place:
            continue
        city = clean(place.group(1)).replace("(MI)", "").strip()
        href = urljoin(source["url"], anchor.get("href", ""))
        events.append(candidate(title, start, f"Centro di {city}", city, text, source, href, "gratuit" in text.lower()))
    return events


def parse_legnanonews(source: dict) -> list[dict]:
    listing = BeautifulSoup(fetch(source["url"]), "html.parser")
    links = []
    for anchor in listing.select("a[href]"):
        label = clean(anchor.get_text(" ", strip=True)).lower()
        href = urljoin(source["url"], anchor.get("href", ""))
        if "weekend" in label and "legnanonews.com" in href and href not in links:
            links.append(href)
    events = []
    city_pattern = r"\b([A-ZÀ-ÖØ-Ý][A-ZÀ-ÖØ-Ý' -]{2,30})\s+[–-]\s+"
    for href in links[:6]:
        soup = BeautifulSoup(fetch(href), "html.parser")
        article = soup.select_one("article") or soup
        for paragraph in article.find_all(["p", "li"]):
            text = clean(paragraph.get_text(" ", strip=True))
            match = re.match(city_pattern, text)
            start = italian_date(text)
            if not match or not start or len(text) < 40:
                continue
            city = clean(match.group(1)).title()
            title = clean(re.split(r"[.–-]", text[len(match.group(0)):], maxsplit=1)[0])
            if len(title) < 5:
                title = text[len(match.group(0)):120]
            venue_match = re.search(r"(?:al|alla|all'|presso|in)\s+((?:parco|piazza|via|villa|castello|sala|cinema|biblioteca|auditorium|centro|spazio|isola)[^,.;]{2,70})", text, re.I)
            venue = clean(venue_match.group(1)) if venue_match else f"Centro di {city}"
            events.append(candidate(title, start, venue, city, text, source, href, bool(re.search(r"gratuit|ingresso libero", text, re.I))))
    return events


def parse_municipal(source: dict) -> list[dict]:
    soup = BeautifulSoup(fetch(source["url"]), "html.parser")
    events = []
    for heading in soup.find_all(["h2", "h3", "h4"]):
        block = heading.find_parent(["article", "li", "div", "section"]) or heading.parent
        text = clean(block.get_text(" ", strip=True))
        start = italian_date(text)
        title = clean(heading.get_text(" ", strip=True))
        if not start or len(title) < 5:
            continue
        anchor = heading.find("a", href=True) or block.find("a", href=True)
        href = urljoin(source["url"], anchor["href"]) if anchor else source["url"]
        venue_match = re.search(r"(?:presso|in|a)\s+((?:piazza|via|viale|parco|biblioteca|auditorium|teatro|centro|chiostro)[^.;]{2,80})", text, re.I)
        venue = clean(venue_match.group(1)) if venue_match else f"Centro di {source['city']}"
        events.append(candidate(title, start, venue, source["city"], text, source, href, bool(re.search(r"gratuit|ingresso libero", text, re.I))))
    return events


def load_cache() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def geocode(query: str, cache: dict) -> tuple[float, float] | None:
    key = normalise(query)
    if key in cache:
        value = cache[key]
        return (value["lat"], value["lng"]) if value else None
    response = SESSION.get("https://nominatim.openstreetmap.org/search", params={"q": query,"format":"jsonv2","limit":1,"countrycodes":"it"}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    coords = (float(payload[0]["lat"]), float(payload[0]["lon"])) if payload else None
    cache[key] = {"lat": coords[0], "lng": coords[1]} if coords else None
    time.sleep(1.05)
    return coords


def distance_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat-a_lat), math.radians(b_lng-a_lng)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return radius * 2 * math.atan2(math.sqrt(h), math.sqrt(1-h))


def duplicate(a: dict, b: dict) -> bool:
    if abs((parse_datetime(a["start"]) - parse_datetime(b["start"])).total_seconds()) > 10800:
        return False
    title_score = SequenceMatcher(None, normalise(a["title"]), normalise(b["title"])).ratio()
    near = distance_km(a["lat"], a["lng"], b["lat"], b["lng"]) < 1.2
    return title_score >= 0.72 and near


def event_id(event: dict) -> str:
    raw = f"{normalise(event['title'])}|{event['start'][:16]}|{round(event['lat'],3)}|{round(event['lng'],3)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:14]


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    previous = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {"events": []}
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=config["horizonDays"])
    source_counts, failures, raw = {}, [], []
    parsers = {"cheventi": parse_cheventi, "legnanonews": parse_legnanonews, "municipal_listing": parse_municipal}
    for source in config["sources"]:
        try:
            found = parsers[source["parser"]](source)
            source_counts[source["name"]] = len(found)
            raw.extend(found)
        except Exception as exc:
            source_counts[source["name"]] = 0
            failures.append({"source": source["name"], "error": str(exc)[:250]})

    cache = load_cache()
    accepted = []
    rejected = {"date":0,"geocode":0,"radius":0,"duplicate":0}
    for event in raw:
        start = parse_datetime(event.get("start", ""))
        if not start or start.astimezone(timezone.utc) < now-timedelta(hours=6) or start.astimezone(timezone.utc) > horizon:
            rejected["date"] += 1; continue
        coords = geocode(f"{event['venue']}, {event['city']}, Lombardia, Italia", cache)
        if not coords:
            coords = geocode(f"{event['city']}, Lombardia, Italia", cache)
        if not coords:
            rejected["geocode"] += 1; continue
        distance = distance_km(config["centre"]["lat"], config["centre"]["lng"], *coords)
        if distance > config["radiusKm"]:
            rejected["radius"] += 1; continue
        event.update({"lat":coords[0],"lng":coords[1],"distanceKm":round(distance,1),"verifiedAt":now.date().isoformat()})
        match = next((existing for existing in accepted if duplicate(event, existing)), None)
        if match:
            rejected["duplicate"] += 1
            if event["confidence"] == "Confermato" or len(event["description"]) > len(match["description"]):
                match.update(event)
            continue
        event["id"] = event_id(event)
        accepted.append(event)

    accepted.sort(key=lambda item: item["start"])
    prior_count = len(previous.get("events", []))
    minimum = config["minimumExpectedEvents"]
    degraded = len(accepted) < minimum or (prior_count >= minimum and len(accepted) < prior_count * config["degradationRatio"])
    if degraded:
        print(f"DEGRADED REFRESH: keeping {prior_count} previous events; new={len(accepted)}, sources={source_counts}, failures={failures}")
        return

    payload = {"updatedAt":now.isoformat(),"mode":"live","radiusKm":config["radiusKm"],"horizonDays":config["horizonDays"],"eventCount":len(accepted),"events":accepted,"sourceHealth":{"sourceCounts":source_counts,"rejected":rejected,"failures":failures}}
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"Published {len(accepted)} events from {sum(source_counts.values())} candidates. {source_counts}; rejected={rejected}")


if __name__ == "__main__":
    main()
