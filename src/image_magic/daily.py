"""Memory of the day, the week ahead, and a Windows notification while the app is open."""

from __future__ import annotations

import random
import sqlite3
import subprocess
import sys
from datetime import date, timedelta

from . import browse, memories, quality

VISIBLE = "a.missing = 0 AND a.duplicate_of IS NULL AND a.kind = 'photo' AND a.private = 0"


def memory_of_day(conn: sqlite3.Connection, today: date | None = None) -> dict | None:
    """The best photo from this date in a past year; otherwise a good random moment."""
    today = today or date.today()
    md = today.strftime("-%m-%d")
    ids = quality.best_of(conn, f"{VISIBLE} AND substr(a.taken_at, 5, 6) = ? AND substr(a.taken_at, 1, 4) != ?", [md, str(today.year)], 1)
    if ids:
        d = browse.photo_detail(conn, ids[0])
        if d:
            memories._with_description(conn, [d])
            years = today.year - int(d["taken_at"][:4])
            place = ", ".join(x for x in [d["city"], d["country"]] if x)
            d["headline"] = f"{years} year{'s' if years != 1 else ''} ago today" + (f": {place}" if place else "")
            d["kind_of"] = "on_this_day"
            return d
    d = memories.shuffle(conn, random.Random(today.toordinal()))      # the same pick all day
    if d:
        place = ", ".join(x for x in [d["city"], d["country"]] if x)
        d["headline"] = f"From {date.fromisoformat(d['taken_at'][:10]):%B %Y}" + (f", {place}" if place else "")
        d["kind_of"] = "shuffle"
    return d


def week_ahead(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """What the coming seven days hold from past years: photo-days, birthdays, trips, milestones."""
    today = today or date.today()
    days = []
    for i in range(7):
        d = today + timedelta(days=i)
        md = d.strftime("-%m-%d")
        rows = conn.execute(
            f"""SELECT substr(a.taken_at, 1, 4) AS y, COUNT(*) AS n FROM assets a WHERE {VISIBLE} AND substr(a.taken_at, 5, 6) = ?
                AND substr(a.taken_at, 1, 4) != ? GROUP BY y ORDER BY y""", (md, str(today.year))).fetchall()
        if rows:
            best = quality.best_of(conn, f"{VISIBLE} AND substr(a.taken_at, 5, 6) = ? AND substr(a.taken_at, 1, 4) != ?", [md, str(today.year)], 1)
            days.append({"date": d.isoformat(), "years": [{"year": r["y"], "n": r["n"]} for r in rows], "cover_id": best[0] if best else None})
    birthdays = []
    try:
        for p in conn.execute("SELECT id, name, birthday FROM persons WHERE name IS NOT NULL AND hidden = 0 AND birthday IS NOT NULL"):
            b = date.fromisoformat(p["birthday"][:10])
            for i in range(7):
                d = today + timedelta(days=i)
                if (d.month, d.day) == (b.month, b.day):
                    birthdays.append({"id": p["id"], "name": p["name"], "date": d.isoformat(), "turns": d.year - b.year})
    except (sqlite3.OperationalError, ValueError):
        pass
    end = today + timedelta(days=6)
    trips = [dict(r) for r in conn.execute(
        "SELECT id, title, start_at, end_at, photo_count, cover_id FROM trips WHERE substr(start_at, 6, 5) BETWEEN ? AND ? "
        "AND substr(start_at, 1, 4) != ? ORDER BY start_at DESC LIMIT 6",
        (today.strftime("%m-%d"), end.strftime("%m-%d"), str(today.year))).fetchall()] if today.month == end.month or today.year == end.year and today.strftime("%m-%d") <= end.strftime("%m-%d") else []
    milestones = [dict(r) for r in conn.execute(
        "SELECT id, title, date, asset_id FROM milestones WHERE state != 'dismissed' AND substr(date, 6, 5) BETWEEN ? AND ? "
        "AND substr(date, 1, 4) != ? ORDER BY date DESC LIMIT 8", (today.strftime("%m-%d"), end.strftime("%m-%d"), str(today.year))).fetchall()]
    return {"from": today.isoformat(), "to": end.isoformat(), "days": days, "birthdays": birthdays, "trips": trips, "milestones": milestones}


def notify(title: str, body: str) -> bool:
    """A desktop notification. Windows: a toast via PowerShell; elsewhere: printed. Never raises."""
    if sys.platform != "win32":
        print(f"{title}: {body}")
        return False
    import xml.sax.saxutils as su
    xml = (f"<toast><visual><binding template='ToastGeneric'><text>{su.escape(title)}</text>"
           f"<text>{su.escape(body)}</text></binding></visual></toast>")
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml($env:IM_TOAST);"
        "$t = New-Object Windows.UI.Notifications.ToastNotification $x;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Image Magic').Show($t)")
    try:
        import os
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env={**os.environ, "IM_TOAST": xml},
                       capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        return False
