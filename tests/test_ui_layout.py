"""The page keeps pop-up menus and dialog action buttons reachable."""
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "src" / "image_magic" / "static" / "index.html"


def test_menu_scrolls_and_can_open_upward():
    page = PAGE.read_text(encoding="utf-8")
    css = page[page.index(".menu {"):page.index(".menu button {")]
    assert "overflow: auto" in css and "max-width" in css
    fn = page[page.index("function showMenu"):page.index("function hideMenu")]
    assert "m.style.maxHeight" in fn and "r.top - 4 - h" in fn   # capped height, flips above the anchor


def test_every_dialog_pins_its_close_and_action_rows():
    page = PAGE.read_text(encoding="utf-8")
    assert ".story .card > .dlg-head { position: sticky" in page
    assert ".story .card > .dlg-foot { position: sticky" in page
    for btn in ("share-go", "film-go", "sugg-add", "rev-accept", "loc-apply", "clean-go", "voice-rec", "story-write"):
        i = page.index(f'id="{btn}"')
        assert 'class="row dlg-foot"' in page[page.rfind("<div class=\"row", 0, i):i], btn
    for close in ("share-close", "loc-close", "sugg-close", "rev-close", "story-close", "clean-close"):
        i = page.index(f'id="{close}"')
        assert "dlg-head" in page[page.rfind("<div class=\"row", 0, i):i], close
