"""Plain-English dates and names in the search box, parsed on the laptop.

"last summer", "christmas 2019", "july 2018", "winter 2017", "easter 2021",
"thanksgiving", "new year's eve 2020", "2019", "emma's 3rd birthday",
"the week we moved to dover", "emma and jack at the beach"
become date ranges, people and a leftover text query. No AI involved.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
SEASONS = {"spring": (3, 5), "summer": (6, 8), "autumn": (9, 11), "fall": (9, 11), "winter": (12, 2)}
ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
           "ninth": 9, "tenth": 10}


def easter(year: int) -> date:
    a = year % 19; b = year // 100; c = year % 100; d = b // 4; e = b % 4; f = (b + 8) // 25
    g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4
    L = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * L) // 451
    month = (h + L - 7 * m + 114) // 31; day = ((h + L - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def thanksgiving(year: int) -> date:
    d = date(year, 11, 1)
    return d + timedelta(days=(3 - d.weekday()) % 7 + 21)


HOLIDAYS = {
    "christmas": lambda y: (date(y, 12, 20), date(y, 12, 27)),
    "xmas": lambda y: (date(y, 12, 20), date(y, 12, 27)),
    "new year": lambda y: (date(y - 1, 12, 30), date(y, 1, 2)),
    "new years": lambda y: (date(y - 1, 12, 30), date(y, 1, 2)),
    "new year's": lambda y: (date(y - 1, 12, 30), date(y, 1, 2)),
    "halloween": lambda y: (date(y, 10, 29), date(y, 11, 1)),
    "thanksgiving": lambda y: (thanksgiving(y) - timedelta(days=1), thanksgiving(y) + timedelta(days=3)),
    "easter": lambda y: (easter(y) - timedelta(days=2), easter(y) + timedelta(days=1)),
    "fourth of july": lambda y: (date(y, 7, 3), date(y, 7, 5)),
    "4th of july": lambda y: (date(y, 7, 3), date(y, 7, 5)),
    "valentine": lambda y: (date(y, 2, 13), date(y, 2, 15)),
    "valentines": lambda y: (date(y, 2, 13), date(y, 2, 15)),
}
FILLER = {"the", "a", "an", "in", "on", "at", "of", "from", "during", "our", "my", "with", "and", "photos", "pictures",
          "pics", "when", "we", "were", "was", "to", "trip", "time", "day", "days", "that", "this"}


def _years(conn: sqlite3.Connection) -> list[int]:
    return [int(r[0]) for r in conn.execute("SELECT DISTINCT substr(taken_at, 1, 4) FROM assets WHERE taken_at IS NOT NULL "
                                            "AND missing = 0 ORDER BY 1") if r[0] and r[0].isdigit()]


def _season(name: str, year: int) -> tuple[date, date]:
    a, b = SEASONS[name]
    if name == "winter":
        return date(year - 1, 12, 1), date(year, 3, 1) - timedelta(days=1)
    end = date(year, b, 1) + timedelta(days=31)
    return date(year, a, 1), date(end.year, end.month, 1) - timedelta(days=1)


def parse(conn: sqlite3.Connection, text: str, today: date | None = None) -> dict:
    """-> {"from", "to", "person", "person2", "q", "label"}; keys only when found."""
    today = today or date.today()
    t = " " + re.sub(r"[^\w' ]+", " ", text.lower()).strip() + " "
    t = t.replace("'s ", " 's ")
    out: dict = {}
    label: list[str] = []

    def take(pattern: str) -> re.Match | None:
        nonlocal t
        m = re.search(pattern, t)
        if m:
            t = t[: m.start()] + " " + t[m.end():]
        return m

    # people (named in the library), possibly two of them
    try:
        people = {(r["name"] or "").lower(): r for r in conn.execute("SELECT id, name, birthday FROM persons WHERE name IS NOT NULL AND hidden = 0")}
    except sqlite3.OperationalError:
        people = {}
    found = []
    for name in sorted(people, key=len, reverse=True):
        if name and take(rf"\b{re.escape(name)}\b"):
            found.append(people[name])
    if found:
        out["person"] = found[0]["id"]
        label.append(found[0]["name"])
        if len(found) > 1:
            out["person2"] = found[1]["id"]
            label.append("and " + found[1]["name"])
    # "<name>'s 3rd birthday" / "first birthday"
    m = take(r"\b(?:'s\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+birthday\b") or take(r"\b(?:'s\s+)?(" + "|".join(ORDINAL) + r")\s+birthday\b")
    if m and found and found[0]["birthday"]:
        n = int(m.group(1)) if m.group(1).isdigit() else ORDINAL[m.group(1)]
        b = date.fromisoformat(found[0]["birthday"][:10])
        try:
            day = b.replace(year=b.year + n)
        except ValueError:
            day = b.replace(year=b.year + n, day=28)
        out["from"], out["to"] = (day - timedelta(days=3)).isoformat(), (day + timedelta(days=3)).isoformat()
        label.append(f"{n}{'st' if n == 1 else 'nd' if n == 2 else 'rd' if n == 3 else 'th'} birthday")
    elif take(r"\b(?:'s\s+)?birthday\b") and found and found[0]["birthday"]:
        md = found[0]["birthday"][5:10]
        out["birthday_md"] = md
        label.append("birthdays")
    # explicit year(s)
    years = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", t)]
    for y in years:
        take(rf"\b{y}\b")
    year = years[0] if years else None
    # relative words
    if take(r"\blast year\b"):
        year = today.year - 1
    elif take(r"\bthis year\b"):
        year = today.year
    rel_last = bool(take(r"\blast\b"))
    rel_this = bool(take(r"\bthis\b"))
    # holidays
    for name in sorted(HOLIDAYS, key=len, reverse=True):
        if take(rf"\b{re.escape(name)}\b"):
            ys = [year] if year else ([today.year - 1] if rel_last else [today.year] if rel_this else _years(conn))
            spans = [HOLIDAYS[name](y) for y in ys]
            if spans:
                if len(spans) == 1:
                    out["from"], out["to"] = spans[0][0].isoformat(), spans[0][1].isoformat()
                else:
                    out["days"] = [(a.isoformat(), b.isoformat()) for a, b in spans]
                label.append(name.title() + (f" {year}" if year else ""))
            break
    # seasons
    for name in SEASONS:
        if take(rf"\b{name}\b"):
            y = year or (today.year - 1 if rel_last or today.month < SEASONS[name][0] else today.year)
            if name == "winter" and not year and rel_last:
                y = today.year - 1 if today.month >= 3 else today.year
            a, b = _season(name, y)
            out["from"], out["to"] = a.isoformat(), b.isoformat()
            label.append(f"{name} {y}")
            break
    # months
    for name in sorted(MONTHS, key=len, reverse=True):
        if take(rf"\b{name}\b"):
            mnum = MONTHS[name]
            y = year or (today.year if mnum <= today.month and not rel_last else today.year - 1)
            out["from"] = date(y, mnum, 1).isoformat()
            out["to"] = (date(y + (mnum == 12), mnum % 12 + 1, 1) - timedelta(days=1)).isoformat()
            label.append(f"{date(y, mnum, 1):%B} {y}")
            break
    if year and "from" not in out and "days" not in out:
        out["from"], out["to"] = f"{year}-01-01", f"{year}-12-31"
        label.append(str(year))
    # "the week/month we moved (to X)": chapter starts
    m = take(r"\b(week|month|day|days)\s+(?:we|i|you)?\s*moved(?:\s+to\s+(\w+))?\b")
    if m:
        chs = conn.execute("SELECT title, start_at FROM chapters ORDER BY start_at").fetchall()[1:]
        if m.group(2):
            chs = [c for c in chs if m.group(2) in c["title"].lower()] or chs
        if chs:
            c = chs[-1]
            s = date.fromisoformat(c["start_at"][:10])
            span = 7 if m.group(1) == "week" else 31 if m.group(1) == "month" else 2
            out["from"], out["to"] = (s - timedelta(days=span)).isoformat(), (s + timedelta(days=span)).isoformat()
            label.append(f"the move to {c['title']}")
    words = [w for w in t.split() if w not in FILLER and w != "'s" and len(w) > 1]
    out["q"] = " ".join(words)
    out["label"] = " · ".join(label)
    return out
