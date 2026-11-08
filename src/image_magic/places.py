"""Offline place names for GPS coordinates, and automatic trips.

Place data: GeoNames cities with population > 1000 (CC BY 4.0, geonames.org),
bundled in data/cities.tsv.gz. No internet needed.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from importlib import resources

CITY_MAX_KM = 30        # beyond this, don't claim a city name
COUNTRY_MAX_KM = 150    # beyond this (open sea, remote areas) leave the place empty
HOME_RADIUS_KM = 100    # photos within this distance of home are not "away"
TRIP_GAP = timedelta(days=3)       # a longer gap between away photos splits a trip
HOME_BREAK = timedelta(hours=36)   # being home this long ends a trip
MIN_TRIP_PHOTOS = 5


@dataclass(frozen=True)
class Place:
    city: str | None
    region: str | None
    country_code: str | None
    country: str | None
    km: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


@lru_cache(maxsize=1)
def countries() -> dict[str, str]:
    raw = (resources.files("image_magic") / "data" / "countries.json").read_text(encoding="utf-8")
    return json.loads(raw)


@lru_cache(maxsize=1)
def _index() -> dict[tuple[int, int], list[tuple[float, float, str, str, str]]]:
    """Places bucketed into 1-degree cells for fast nearest lookups."""
    grid: dict[tuple[int, int], list] = defaultdict(list)
    path = resources.files("image_magic") / "data" / "cities.tsv.gz"
    with path.open("rb") as raw, gzip.open(raw, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            lat, lon, name, region, cc = line.rstrip("\n").split("\t")
            la, lo = float(lat), float(lon)
            grid[(math.floor(la), math.floor(lo))].append((la, lo, name, region, cc))
    return dict(grid)


@lru_cache(maxsize=1)
def _all_places() -> list[tuple[float, float, str, str, str]]:
    return [p for cell in _index().values() for p in cell]


US_STATES = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas", "ca": "California", "co": "Colorado", "ct": "Connecticut",
    "de": "Delaware", "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho", "il": "Illinois", "in": "Indiana", "ia": "Iowa",
    "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland", "ma": "Massachusetts", "mi": "Michigan",
    "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada", "nh": "New Hampshire",
    "nj": "New Jersey", "nm": "New Mexico", "ny": "New York", "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma",
    "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina", "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas",
    "ut": "Utah", "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming", "dc": "Washington, D.C.",
}
_ABBREV = {"st": "saint", "st.": "saint", "ste": "sainte", "ste.": "sainte", "ft": "fort", "ft.": "fort", "mt": "mount", "mt.": "mount", "pt": "point", "pt.": "point"}
_STREET = re.compile(r"\b(\d+[a-z]?|hwy|highway|state|route|rt|rd|road|st(?:reet)?|ave(?:nue)?|blvd|boulevard|dr(?:ive)?|ln|lane|ct|court|pl|way|suite|ste|apt|unit|n|s|e|w|north|south|east|west)\b\.?", re.I)
_ZIP = re.compile(r"\b\d{5}(?:-\d{4})?\b|\b[a-z]\d[a-z] ?\d[a-z]\d\b|\b[a-z]{1,2}\d{1,2}[a-z]? ?\d[a-z]{2}\b", re.I)
_COORD = re.compile(r"^\s*([+-]?\d{1,2}(?:\.\d+)?)\s*[, ]\s*([+-]?\d{1,3}(?:\.\d+)?)\s*$")
# 37°49'41.2"N 92°10'39.7"W  (what Google Maps shows when you click a spot), also 37 49 41.2 N
_DMS = re.compile(r"""(\d{1,3})[°\s]\s*(\d{1,2})['′\s]\s*(\d{1,2}(?:\.\d+)?)?["″\s]*\s*([NSEW])""", re.I)
_URL_PATTERNS = (
    re.compile(r"/@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)"),                  # google.com/maps/.../@37.82,-92.17,15z
    re.compile(r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)"),                # the place marker inside a Google link
    re.compile(r"[?&](?:q|ll|query|center|destination|daddr|saddr|sll)=(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)"),  # maps?q=.., Apple ?ll=..
    re.compile(r"/place/(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)"),
    re.compile(r"/search/(-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)"),
)
SHORT_LINK = re.compile(r"^https?://(maps\.app\.goo\.gl|goo\.gl/maps|g\.co/kgs|maps\.apple\.com/p)/", re.I)


def parse_coords(text: str) -> tuple[float, float] | None:
    """Coordinates out of anything someone can copy from a map: "37.8281, -92.1777", the degrees form
    Google shows, or a Google Maps / Apple Maps link. None if there are none."""
    t = (text or "").strip()
    if not t:
        return None
    m = _COORD.match(t)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        return (lat, lon) if abs(lat) <= 90 and abs(lon) <= 180 else None
    parts = _DMS.findall(t)
    if len(parts) >= 2:
        vals = {}
        for d, mi, s, hemi in parts[:2]:
            v = float(d) + float(mi) / 60 + (float(s) if s else 0.0) / 3600
            hemi = hemi.upper()
            vals["lat" if hemi in "NS" else "lon"] = -v if hemi in "SW" else v
        if "lat" in vals and "lon" in vals and abs(vals["lat"]) <= 90 and abs(vals["lon"]) <= 180:
            return (round(vals["lat"], 6), round(vals["lon"], 6))
    if "://" in t or t.lower().startswith(("maps.", "www.", "google.", "goo.gl")):
        import urllib.parse
        u = urllib.parse.unquote_plus(t)
        for pat in _URL_PATTERNS:
            m = pat.search(u)
            if m:
                lat, lon = float(m.group(1)), float(m.group(2))
                if abs(lat) <= 90 and abs(lon) <= 180:
                    return (lat, lon)
    return None


def expand_short_link(url: str, timeout: float = 10) -> str:
    """Follow a maps.app.goo.gl / goo.gl/maps redirect to the full Google Maps URL (one request, nothing else sent)."""
    import urllib.request
    from . import net
    req = urllib.request.Request(url.strip(), method="HEAD", headers={"User-Agent": "ImageMagic/1.0 (personal photo library; local app)"})
    try:
        with net.urlopen(req, timeout=timeout) as resp:
            return resp.geturl()
    except Exception:
        req = urllib.request.Request(url.strip(), headers={"User-Agent": "ImageMagic/1.0 (personal photo library; local app)"})
        with net.urlopen(req, timeout=timeout) as resp:
            resp.read(1)
            return resp.geturl()


def _expand(words: str) -> str:
    return " ".join(_ABBREV.get(w, w) for w in words.split())


def parse_query(query: str) -> dict:
    """Pull the useful parts out of what someone typed: a place name, a region/state, a country, or coordinates.

    "14400 State Hwy Z, St Robert, MO 65584, United States" -> name "saint robert", region "missouri", country "united states".
    """
    q = (query or "").strip()
    c = parse_coords(q)
    if c:
        return {"lat": c[0], "lon": c[1]}
    if "://" in q:
        return {"names": [], "region": None, "country": None, "link": True}    # a map link with no coordinates in it: expand it online
    parts = [p.strip() for p in q.replace(";", ",").split(",") if p.strip()]
    cc = {v.lower(): k for k, v in countries().items()}
    cc["usa"] = cc["us"] = "US"; cc["uk"] = "GB"; cc["united states of america"] = "US"
    out: dict = {"names": [], "region": None, "country": None}
    for n_, part in enumerate(parts):
        low = part.lower()
        tokens = low.split()
        if tokens and tokens[0] in US_STATES and (len(tokens) == 1 or _ZIP.fullmatch(" ".join(tokens[1:]))):
            out["region"] = US_STATES[tokens[0]].lower(); out["country"] = out["country"] or "US"    # "DE" is Delaware here, not Germany
            continue
        if low in cc or (low.upper() in countries() and len(low) == 2):
            out["country"] = cc.get(low) or low.upper()
            continue
        if re.match(r"^\d+[a-z]?\s", low) and len(parts) > 1:
            continue                                     # a street line ("14400 State Hwy Z"): the town is in another part
        if low in {v.lower() for v in US_STATES.values()}:
            out["region"] = low; out["country"] = out["country"] or "US"
            continue
        cleaned = _ZIP.sub(" ", low)
        if re.search(r"\d", cleaned) or _STREET.search(cleaned) and len(_STREET.sub(" ", cleaned).split()) == 0:
            cleaned = _STREET.sub(" ", cleaned)          # a street address: nothing here names a town
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        if cleaned:
            out["names"].append(_expand(cleaned))
    if not out["names"] and parts:
        out["names"].append(_expand(re.sub(r"\s+", " ", _ZIP.sub(" ", parts[0].lower())).strip()))
    return out


def search(query: str, limit: int = 8) -> list[dict]:
    """Places matching what was typed: a name, "Town, ST", a full postal address, or "lat, lon". Offline."""
    parsed = parse_query(query)
    cc = countries()
    if "lat" in parsed:
        p = lookup(parsed["lat"], parsed["lon"])
        near = f"near {p.city}, {p.country}" if p.city else (p.country or "")
        return [{"lat": parsed["lat"], "lon": parsed["lon"], "name": f"{parsed['lat']:.4f}, {parsed['lon']:.4f}", "region": near,
                 "country_code": p.country_code or "", "country": ""}]
    names = [n for n in parsed["names"] if len(n) >= 2]
    if not names:
        return []
    region, country = parsed["region"], parsed["country"]
    scored: list[tuple[int, int, tuple]] = []
    for la, lo, name, reg, code in _all_places():
        n = name.lower()
        best = None
        for rank, q in enumerate(names):
            if n == q:
                best = 0
            elif n.startswith(q):
                best = 1 if best is None else min(best, 1)
            elif q in n:
                best = 2 if best is None else min(best, 2)
        if best is None:
            continue
        if country and code != country:
            best += 3                                   # a hit in another country still shows, further down
        if region and (reg or "").lower() != region:
            best += 3
        scored.append((best, len(name), (la, lo, name, reg, code)))
        if len(scored) >= limit * 40:
            break
    scored.sort(key=lambda t: t[0])                      # stable: within a rank the bigger places (file order) come first
    out = []
    for _, _, (la, lo, name, reg, code) in scored[:limit]:
        out.append({"lat": la, "lon": lo, "name": name, "region": reg or "", "country_code": code, "country": cc.get(code, code)})
    return out


def geocode_online(query: str, timeout: float = 10) -> list[dict]:
    """Street addresses are not in the bundled list. This asks OpenStreetMap's Nominatim, sending only the text typed.
    Called only when you click 'Look it up online'."""
    import urllib.parse
    import urllib.request
    from . import net
    q = (query or "").strip()
    if len(q) < 3:
        return []
    if "://" in q:                                   # a short map link: follow it once and read the coordinates
        full = expand_short_link(q, timeout) if SHORT_LINK.match(q) else q
        c = parse_coords(full)
        if not c:
            return []
        p = lookup(c[0], c[1])
        near = f"near {p.city}, {p.country}" if p.city else (p.country or "")
        return [{"lat": c[0], "lon": c[1], "name": f"{c[0]:.4f}, {c[1]:.4f}", "region": near, "country_code": p.country_code or "",
                 "country": "", "online": True}]
    url = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&q=" + urllib.parse.quote(q)
    req = urllib.request.Request(url, headers={"User-Agent": "ImageMagic/1.0 (personal photo library; local app)"})
    with net.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read(200_000).decode("utf-8", "replace"))
    out = []
    for r in data if isinstance(data, list) else []:
        try:
            la, lo = float(r["lat"]), float(r["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        label = str(r.get("display_name") or "")
        bits = [b.strip() for b in label.split(",")]
        out.append({"lat": la, "lon": lo, "name": ", ".join(bits[:2]) or f"{la:.4f}, {lo:.4f}", "region": ", ".join(bits[2:4]),
                    "country_code": "", "country": bits[-1] if bits else "", "online": True})
    return out


def lookup(lat: float, lon: float) -> Place:
    grid = _index()
    best, best_km = None, float("inf")
    cy, cx = math.floor(lat), math.floor(lon)
    # Search outward ring by ring; ~111 km per ring of latitude.
    for ring in range(0, 3):
        for dy in range(-ring, ring + 1):
            for dx in range(-ring, ring + 1):
                if max(abs(dy), abs(dx)) != ring:
                    continue
                gx = ((cx + dx + 180) % 360) - 180
                for p in grid.get((cy + dy, gx), ()):
                    d = haversine_km(lat, lon, p[0], p[1])
                    if d < best_km:
                        best, best_km = p, d
        if best is not None and best_km <= ring * 111:
            break
    if best is None or best_km > COUNTRY_MAX_KM:
        return Place(None, None, None, None, best_km if best else -1.0)
    _, _, name, region, cc = best
    return Place(
        city=name if best_km <= CITY_MAX_KM else None,
        region=region or None,
        country_code=cc,
        country=countries().get(cc, cc),
        km=round(best_km, 1),
    )


def update_places(conn: sqlite3.Connection) -> int:
    """Fill in place names for photos that have GPS but no place yet."""
    rows = conn.execute(
        "SELECT id, lat, lon FROM assets WHERE lat IS NOT NULL AND place_km IS NULL"
    ).fetchall()
    for row in rows:
        p = lookup(row["lat"], row["lon"])
        conn.execute(
            "UPDATE assets SET city = ?, region = ?, country_code = ?, country = ?, place_km = ? WHERE id = ?",
            (p.city, p.region, p.country_code, p.country, p.km, row["id"]),
        )
    conn.commit()
    return len(rows)


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt.replace(tzinfo=None)


def _homes_by_year(photos: list) -> dict[int, tuple[float, float]]:
    """Home = the most photographed city (or ~10 km cell) in each year."""
    by_year: dict[int, Counter] = defaultdict(Counter)
    spot: dict[tuple, tuple[float, float]] = {}
    for p in photos:
        key = (p["country_code"], p["city"]) if p["city"] else (round(p["lat"], 1), round(p["lon"], 1))
        by_year[p["t"].year][key] += 1
        spot.setdefault(key, (p["lat"], p["lon"]))
    homes = {y: spot[c.most_common(1)[0][0]] for y, c in by_year.items()}
    # Years with few photos inherit the nearest busier year's home.
    overall = Counter()
    for c in by_year.values():
        overall.update(c)
    fallback = spot[overall.most_common(1)[0][0]] if overall else None
    for y, c in by_year.items():
        if sum(c.values()) < 20 and fallback:
            homes[y] = fallback
    return homes


def rebuild_trips(conn: sqlite3.Connection) -> int:
    """Group photos taken away from home into trips. Returns the number of trips."""
    rows = conn.execute(
        """SELECT id, taken_at, lat, lon, city, country_code, country, trip_manual FROM assets
           WHERE lat IS NOT NULL AND taken_at IS NOT NULL AND missing = 0
             AND duplicate_of IS NULL AND kind = 'photo'
           ORDER BY taken_at"""
    ).fetchall()
    photos = []
    for r in rows:
        try:
            photos.append({**dict(r), "t": _parse(r["taken_at"])})
        except ValueError:
            continue
    photos.sort(key=lambda p: p["t"])

    from . import learning
    trip_gap = timedelta(days=learning.get(conn, "trip.gap_days", TRIP_GAP.days))
    min_photos = int(learning.get(conn, "trip.min_photos", MIN_TRIP_PHOTOS))
    groups: list[list[dict]] = []
    if photos:
        homes = _homes_by_year(photos)
        current: list[dict] = []
        last_away: datetime | None = None
        home_since: datetime | None = None
        for p in photos:
            hlat, hlon = homes[p["t"].year]
            away = haversine_km(p["lat"], p["lon"], hlat, hlon) > HOME_RADIUS_KM
            if away:
                broke = (
                    last_away is None
                    or p["t"] - last_away > trip_gap
                    or (home_since is not None and p["t"] - home_since > HOME_BREAK)
                )
                if broke and current:
                    groups.append(current)
                    current = []
                current.append(p)
                last_away, home_since = p["t"], None
            elif current and home_since is None:
                home_since = p["t"]
        if current:
            groups.append(current)
    groups = [g for g in groups if len(g) >= min_photos]

    # Keep what you decided: manual assignments, trips you created, titles you set.
    existing = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM trips")}
    keep_ids = {t["id"] for t in existing.values() if t["manual"]}
    keep_ids |= {r[0] for r in conn.execute("SELECT DISTINCT trip_id FROM assets WHERE trip_manual = 1 AND trip_id IS NOT NULL")}
    conn.execute("UPDATE assets SET trip_id = NULL WHERE trip_manual = 0")
    matched: set[int] = set()
    for g in groups:
        g = [p for p in g if not p["trip_manual"]]
        if len(g) < min_photos:
            continue
        country_counts = Counter(p["country"] for p in g if p["country"])
        names = [c for c, _ in country_counts.most_common(3)]
        title = " & ".join(names[:2]) + (" +" if len(names) > 2 else "") if names else "Trip"
        cities = [c for c, _ in Counter(p["city"] for p in g if p["city"]).most_common(3)]
        start, end = g[0]["t"], g[-1]["t"]
        cover = g[len(g) // 2]
        lat, lon = sum(p["lat"] for p in g) / len(g), sum(p["lon"] for p in g) / len(g)
        # Reuse an existing automatic trip whose dates overlap, so ids (and stories) stay stable.
        trip_id = None
        for t in existing.values():
            if t["id"] in matched or t["manual"]:
                continue
            if t["start_at"][:10] <= end.isoformat()[:10] and t["end_at"][:10] >= start.isoformat()[:10]:
                trip_id = t["id"]
                break
        if trip_id is None:
            cur = conn.execute(
                """INSERT INTO trips (title, subtitle, start_at, end_at, photo_count, cover_id, lat, lon)
                   VALUES (?, ?, ?, ?, 0, ?, ?, ?)""",
                (title, ", ".join(cities), start.isoformat(), end.isoformat(), cover["id"], lat, lon))
            trip_id = cur.lastrowid
        else:
            t = existing[trip_id]
            conn.execute("UPDATE trips SET title = ?, subtitle = ?, start_at = ?, end_at = ?, cover_id = ?, lat = ?, lon = ? WHERE id = ?",
                         (t["title"] if t["title_locked"] else title, ", ".join(cities), start.isoformat(), end.isoformat(),
                          t["cover_id"] or cover["id"], lat, lon, trip_id))
        matched.add(trip_id)
        # Every photo/video in the trip's time window joins the trip: videos (located
        # or not) and photos without GPS (from apps that strip location).
        id_list = [p["id"] for p in g]
        for i in range(0, len(id_list), 500):
            chunk = id_list[i:i + 500]
            conn.execute(
                f"""UPDATE assets SET trip_id = ? WHERE trip_manual = 0 AND id IN ({','.join('?' * len(chunk))})""",
                [trip_id, *chunk])
        conn.execute(
            """UPDATE assets SET trip_id = ? WHERE trip_manual = 0 AND missing = 0 AND kind != 'live_video'
               AND taken_at IS NOT NULL AND (lat IS NULL OR kind != 'photo') AND taken_at >= ? AND taken_at <= ?""",
            (trip_id, start.isoformat(), (end + timedelta(seconds=1)).isoformat()))
    for tid in list(existing):
        if tid not in matched and tid not in keep_ids:
            conn.execute("DELETE FROM trips WHERE id = ?", (tid,))
    for tid in matched | keep_ids:
        r = conn.execute("SELECT COUNT(*) AS n, MIN(taken_at) AS s, MAX(taken_at) AS e FROM assets "
                         "WHERE trip_id = ? AND duplicate_of IS NULL AND missing = 0", (tid,)).fetchone()
        if r["n"]:
            conn.execute("UPDATE trips SET photo_count = ?, start_at = ?, end_at = ? WHERE id = ?", (r["n"], r["s"], r["e"], tid))
        elif tid not in keep_ids:
            conn.execute("DELETE FROM trips WHERE id = ?", (tid,))
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]


def refresh(conn: sqlite3.Connection) -> tuple[int, int]:
    """Look up new places and rebuild trips. Returns (places_added, trips)."""
    return update_places(conn), rebuild_trips(conn)
