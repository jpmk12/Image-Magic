"""Albums: hand-picked and smart."""
import pytest

from image_magic import albums, books, browse
from image_magic.server import find_photos
from image_magic.settings import Settings
from test_ask import library  # noqa: F401


def test_manual_and_smart_albums(library):  # noqa: F811
    conn, lib = library
    jo = [r[0] for r in conn.execute("SELECT id FROM assets WHERE path LIKE 'jo/%' ORDER BY taken_at")]
    home = [r[0] for r in conn.execute("SELECT id FROM assets WHERE path LIKE 'home/%' ORDER BY taken_at")]
    a = albums.create(conn, "Favourite moments", ids=[jo[2], home[0], jo[0]])
    assert albums.add(conn, a, [jo[0], home[1]]) == 1                    # one new, one already there
    items = browse.photos(conn, {"album": a})["items"]
    assert {i["id"] for i in items} == {jo[2], home[0], jo[0], home[1]}
    # a manual album replays in the order you added, not by date
    assert [i["id"] for i in browse.replay(conn, {"album": a})] == [jo[2], home[0], jo[0], home[1]]
    albums.remove(conn, a, [home[1]])
    assert len(browse.ids(conn, {"album": a})) == 3
    assert albums.albums_of(conn, jo[0]) == [{"id": a, "title": "Favourite moments"}]
    # a smart album is a saved search that follows the library
    s = albums.create(conn, "Jordan", kind="smart", filters={"country": "JO", "kind": "photo", "junk": "x"})
    assert albums.get(conn, s)["filters"] == {"country": "JO", "kind": "photo"}
    assert len(browse.ids(conn, {"album": s})) == 12
    conn.execute("UPDATE assets SET country_code = 'JO', country = 'Jordan' WHERE id = ?", (home[5],))
    assert len(browse.ids(conn, {"album": s})) == 13
    with pytest.raises(ValueError):
        albums.add(conn, s, [home[0]])
    with pytest.raises(ValueError):
        albums.create(conn, "x", kind="smart", filters={})
    listing = {x["title"]: x for x in albums.listing(conn)}
    assert listing["Jordan"]["photo_count"] == 13 and listing["Favourite moments"]["cover_id"] in {jo[2], home[0], jo[0]}
    # a smart album with a text query runs the search
    q = albums.create(conn, "Cliffs", kind="smart", filters={"q": "cliffs", "country": "JO"})
    hits = find_photos(conn, Settings(apple_id="x", library_path=str(lib)), {"album": q})
    assert len(hits) == 3                                                # the three captioned Petra photos
    albums.rename(conn, a, "Best of us")
    f = books.make(conn, lib / "originals", lib, "album", str(a))
    assert f.name == "Best-of-us-album-book.pdf"
    albums.delete(conn, a)
    assert albums.get(conn, a) is None and conn.execute("SELECT COUNT(*) FROM album_items WHERE album_id = ?", (a,)).fetchone()[0] == 0
