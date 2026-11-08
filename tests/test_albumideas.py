"""Album ideas (themes, people sets, places, events), find-more for albums, learned signal weights."""
from datetime import datetime, timedelta

from image_magic import albumideas, albums, catalog, inbox, learning, search
from test_places_browse import HOME, PETRA, make_photo
from test_search import FakeModel


def _lib(tmp_path, n_red=45, n_blue=45, n_green=5):
    lib = tmp_path / "lib"
    orig = lib / "originals"
    t = datetime(2019, 1, 1, 12)
    k = 0
    for color, n in (((200, 20, 20), n_red), ((20, 20, 200), n_blue), ((20, 200, 20), n_green)):
        for i in range(n):
            make_photo(orig / f"p{k:03d}.jpg", t + timedelta(days=k), HOME if k % 3 else None, color=color); k += 1
    conn = catalog.connect(lib / "library.db")
    catalog.scan(conn, orig)
    search.index_photos(conn, lib, orig, FakeModel())
    return conn, lib, orig


def test_theme_ideas_from_the_visual_index(tmp_path):
    conn, lib, orig = _lib(tmp_path)
    ideas = albumideas.theme_ideas(conn, str(lib / "library.db"))
    assert len(ideas) == 2                                         # red and blue; green is too small
    for idea in ideas:
        assert idea["kind"] == "theme" and len(idea["ids"]) >= 30 and idea["title"] == "Look-alikes" and 0.5 <= idea["conf"] <= 0.9
    # the inbox offers them as album cards; accepting makes the album; skipping hides the theme for good
    cards = [c for c in inbox._album_ideas(conn, str(lib / "library.db")) if c["action"]["idea"]["kind"] == "theme"]
    assert len(cards) == 2 and cards[0]["kind"] == "album" and cards[0]["title"].startswith("Album idea: Look-alikes (")
    r = inbox.apply(conn, lib, orig, inbox.restrict(cards[0], cards[0]["ids"][:35]), name="Red things")
    assert r["summary"] == "Album made with 35 photos: Red things"
    a = albums.listing(conn)[0]
    assert a["title"] == "Red things" and a["photo_count"] == 35
    inbox.skip(conn, cards[1])
    assert albumideas.theme_ideas(conn, str(lib / "library.db")) == []        # one is an album now, the other was skipped
    assert inbox.undo_last(conn, lib, orig) == cards[0]["title"]
    assert albums.listing(conn) == []


def test_people_place_and_event_ideas(tmp_path):
    from image_magic import events, faces
    conn, lib, orig = _lib(tmp_path, n_red=20, n_blue=20, n_green=0)
    faces.ensure_schema(conn)
    for name in ("Emma", "Sam"):
        conn.execute("INSERT INTO persons (name) VALUES (?)", (name,))
    emma, sam = [r[0] for r in conn.execute("SELECT id FROM persons ORDER BY id")]
    ids = [r[0] for r in conn.execute("SELECT id FROM assets ORDER BY id")]
    def face(aid, pid):
        conn.execute("INSERT INTO faces (asset_id, person_id, x1, y1, x2, y2, score, kps, size_px) VALUES (?, ?, 0, 0, .2, .2, .9, '[]', 100)", (aid, pid))
    for aid in ids[:16]:
        face(aid, emma); face(aid, sam)                          # 16 photos of the two together
    for aid in ids[16:20]:
        face(aid, emma)                                           # Emma alone, but too few for a solo album
    conn.commit()
    ps = albumideas.people_set_ideas(conn)
    assert [p["title"] for p in ps] == ["Emma & Sam together"] and len(ps[0]["ids"]) == 16
    albumideas.reject(conn, "people", ps[0]["ref"])
    assert albumideas.people_set_ideas(conn) == []
    # a spot visited on many days: the located photos all share HOME here, on 26 different days
    pl = albumideas.place_ideas(conn)
    assert len(pl) == 1 and pl[0]["title"].startswith("Home") and len(pl[0]["ids"]) >= 20
    albums.create(conn, pl[0]["title"], ids=pl[0]["ids"][:3])
    assert albumideas.place_ideas(conn) == []                     # an album with that name exists
    # a big event with two named people
    for i, aid in enumerate(ids):
        conn.execute("UPDATE assets SET taken_at = ? WHERE id = ?", ((datetime(2019, 6, 8, 14) + timedelta(minutes=5 * i)).isoformat(), aid))
    conn.commit()
    events.rebuild(conn)
    ev = conn.execute("SELECT id, title, photo_count FROM events ORDER BY photo_count DESC").fetchone()
    assert ev["photo_count"] == 40
    albumideas.EVENT_MIN_PHOTOS = 10
    try:
        ei = albumideas.event_ideas(conn)
        assert len(ei) == 1 and ei[0]["ref"] == str(ev["id"]) and "Emma, Sam" in ei[0]["why"]
    finally:
        albumideas.EVENT_MIN_PHOTOS = 40


def test_find_more_for_an_album(tmp_path):
    conn, lib, orig = _lib(tmp_path, n_red=12, n_blue=12, n_green=0)
    red = [r[0] for r in conn.execute("SELECT id FROM assets ORDER BY id LIMIT 12")]
    a = albums.create(conn, "Reds", ids=red[:4])
    s = albumideas.suggest_for_album(conn, str(lib / "library.db"), a)
    got = {i["id"] for i in s["items"]}
    assert got == set(red[4:]) and all("looks like the album" in i["reasons"][0] for i in s["items"])
    albumideas.reject_for_album(conn, a, red[4:6])
    s2 = albumideas.suggest_for_album(conn, str(lib / "library.db"), a)
    assert {i["id"] for i in s2["items"]} == set(red[6:])
    assert albumideas.suggest_for_album(conn, str(lib / "library.db"), albums.create(conn, "Empty"))["items"] == []


def test_signal_weights_learn_from_accepts_and_rejects(tmp_path):
    from image_magic import curate
    conn = catalog.connect(tmp_path / "lib" / "library.db")
    assert curate.signal_of("shot between two photos of the trip (IMG_1 and IMG_2)") == "shot"
    assert curate.signal_of("shows Emma, who is on this trip") == "people" and curate.signal_of("taken 2 days from the trip") == "time"
    assert curate.signal_of("a travel day: airport") == "travel" and curate.signal_of("looks like the trip's photos (80%)") == "looks"
    action = {"do": "trip_add", "trip": 1, "ids": [1, 2, 3], "signals": {"1": ["time", "looks"], "2": ["looks"], "3": ["words"]}}
    for _ in range(4):
        inbox._log_signals(conn, action, accepted=[1, 2], rejected=[3])
    assert "signal.looks" not in learning.learn(conn)                                              # eight decisions: not yet
    for _ in range(6):
        inbox._log_signals(conn, action, accepted=[1, 2], rejected=[3])
    learned = learning.learn(conn)
    assert learned["signal.looks"]["value"] == 1.5 and learned["signal.words"]["value"] == 0.5     # always kept / never kept
    assert learned["signal.time"]["value"] == 1.5 and "signal.shot" not in learned
    assert learning.get(conn, "signal.looks", 1.0) == 1.5 and learning.get(conn, "signal.shot", 1.0) == 1.0
