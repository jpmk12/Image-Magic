"""Flying log and the strict share filter."""
import json

from image_magic import browse, flying, share
from test_ask import library  # noqa: F401


def _describe(conn, aid, caption, keywords=(), text=""):
    conn.execute("INSERT OR REPLACE INTO descriptions (asset_id, model, caption, keywords, text, created) VALUES (?, 'm', ?, ?, ?, 'now')",
                 (aid, caption, json.dumps(list(keywords)), text))


def test_flying_log_groups_by_base_day_and_tail(library):  # noqa: F811
    conn, lib = library
    ids = [r[0] for r in conn.execute("SELECT id FROM assets WHERE path LIKE 'home/%' ORDER BY taken_at LIMIT 6")]
    _describe(conn, ids[0], "A C-17 Globemaster on the flight line.", ["aircraft", "c-17"], "07-7171")
    _describe(conn, ids[1], "Grey cargo jet taxiing.", ["aircraft"], "")
    _describe(conn, ids[2], "A pilot's ID badge on a desk.", ["aircraft", "badge"], "SMITH 1234")
    _describe(conn, ids[3], "Kids on the beach.", ["beach"], "")
    conn.commit()
    assert flying.refresh(conn) == 3
    f = flying.log(conn)
    assert f["total"] == 3 and f["bases"][0]["photos"] == 3 and f["tails"] == [
        {"tail": "07-7171", "photos": 1, "first": f["tails"][0]["first"], "last": f["tails"][0]["last"], "cover_id": ids[0]}]
    assert len(f["sorties"]) == 3 and f["sorties"][-1]["tails"] == ["07-7171"]
    assert {i["id"] for i in browse.photos(conn, {"flying": "1"})["items"]} == set(ids[:3])
    # strict share: the tail-number photo and the badge are removed automatically; the page has no captions or places
    r = share.check(conn, {"flying": "1"})
    assert r["strict"] and {x["id"] for x in r["removed"]} == {ids[0], ids[2]} and r["total"] == 1
    folder = share.export(conn, lib / "originals", lib, {"flying": "1"}, "Flying")
    page = (folder / "index.html").read_text(encoding="utf-8")
    assert page.count("<figure>") == 1 and "cargo jet" not in page and "Dover" not in page and "2019</figcaption>" in page
    # the ordinary check still only flags
    r2 = share.check(conn, {"year": "2019"})
    assert not r2["strict"] and len(r2["flagged"]) >= 2 and r2["removed"] == []
