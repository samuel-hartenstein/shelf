"""Scanning a household's odd items: keep what no database knows, own two of
something, find a game by its barcode, and say why Store mode skipped a code."""

import pytest

from app.routers import items_common
from app.services import provider_result
from app.services import tags as tags_svc
from tests.conftest import _insert_item, _insert_location

SCHOOL_BOOK = "9783849005351"


@pytest.fixture
def nothing_found(monkeypatch):
    async def _lookup(isbn13, hc_token, client, *, google_api_key=None):
        cascade = provider_result.combine([provider_result.no_match("openlibrary")], provider="isbn-cascade")
        return None, "manual", {}, cascade

    async def _preview(isbn13, client):
        return None
    monkeypatch.setattr(items_common, "_lookup_metadata", _lookup)
    monkeypatch.setattr(items_common, "_fetch_preview_cover", _preview)


def _tags(db, item_id):
    return {t["name"] for t in tags_svc.get_item_tags(db, item_id)}


class TestSaveAnyway:
    def test_the_not_found_card_offers_it(self, editor_client, nothing_found):
        page = editor_client.post("/api/scan", data={"isbn": SCHOOL_BOOK, "media_type": "book", "mode": "add"}).text
        assert 'data-testid="save-unresolved"' in page
        assert '&#34;unresolved&#34;: &#34;1&#34;' in page or '"unresolved": "1"' in page

    def test_it_files_the_code_tagged_unresolved(self, editor_client, db):
        loc = _insert_location(db, "Arbeitszimmer")
        db.commit()
        resp = editor_client.post("/api/items/manual", data={
            "isbn": SCHOOL_BOOK, "media_type": "book", "unresolved": "1", "location_id": str(loc)})
        assert 'data-scan-status="added"' in resp.text
        row = db.execute("SELECT id, title, isbn, location_id FROM items_live WHERE isbn = ?",
                         (SCHOOL_BOOK,)).fetchone()
        assert (row["title"], row["location_id"]) == (SCHOOL_BOOK, loc)
        assert _tags(db, row["id"]) == {"Unresolved"}

    def test_the_tag_is_in_the_library_language(self, editor_client, db, monkeypatch):
        monkeypatch.setenv("SHELF_I18N", "on")
        monkeypatch.setenv("SHELF_DEFAULT_LANGUAGE", "de")
        editor_client.post("/api/items/manual", data={
            "isbn": SCHOOL_BOOK, "media_type": "book", "unresolved": "1", "tags": "Schule"})
        item_id = db.execute("SELECT id FROM items_live WHERE isbn = ?", (SCHOOL_BOOK,)).fetchone()["id"]
        assert _tags(db, item_id) == {"Schule", "Ungeklärt"}

    def test_without_the_flag_a_title_is_still_required(self, editor_client, db):
        resp = editor_client.post("/api/items/manual", data={"isbn": SCHOOL_BOOK, "media_type": "book"})
        assert 'data-scan-status="error"' in resp.text
        assert db.execute("SELECT COUNT(*) FROM items_live").fetchone()[0] == 0


class TestAnotherCopy:
    def test_the_duplicate_card_offers_it(self, editor_client, db):
        _insert_item(db, title="Dune", isbn="9780441013593")
        db.commit()
        resp = editor_client.post("/api/items/manual", data={
            "isbn": "9780441013593", "media_type": "book", "title": "Dune"})
        assert 'data-scan-status="duplicate"' in resp.text
        assert 'data-testid="scan-add-copy"' in resp.text

    def test_a_second_copy_lands_at_the_scan_location(self, editor_client, db):
        living, archive = _insert_location(db, "Wohnzimmer"), _insert_location(db, "Archiv")
        db.commit()
        item_id = _insert_item(db, title="Murder", isbn=None, media_type="board_game",
                               upc="5060756414005", location_id=living)
        db.commit()
        resp = editor_client.post(f"/api/items/{item_id}/scan-copy",
                                  data={"location_id": str(archive), "code": "5060756414005", "mode": "add"})
        assert 'data-scan-status="copy_added"' in resp.text
        assert "Archiv" in resp.text
        rows = db.execute("SELECT copy_number, location_id, is_primary FROM copies_live "
                          "WHERE item_id = ? ORDER BY copy_number", (item_id,)).fetchall()
        assert [tuple(r) for r in rows] == [(1, living, 1), (2, archive, 0)]
        assert db.execute("SELECT location_id FROM items_live WHERE id = ?", (item_id,)).fetchone()[0] == living

    def test_an_item_filed_without_a_location_ends_up_with_two_copies(self, editor_client, db):
        shelf = _insert_location(db, "Regal")
        item_id = _insert_item(db, title="Dune", isbn="9780441013593")
        db.commit()
        editor_client.post(f"/api/items/{item_id}/scan-copy", data={"location_id": str(shelf)})
        rows = db.execute("SELECT location_id FROM copies_live WHERE item_id = ? ORDER BY copy_number",
                          (item_id,)).fetchall()
        assert [r[0] for r in rows] == [None, shelf]

    def test_recent_scans_name_it(self, editor_client, db):
        item_id = _insert_item(db, title="Dune", isbn="9780441013593")
        db.commit()
        editor_client.post(f"/api/items/{item_id}/scan-copy", data={"code": "9780441013593"})
        assert db.execute("SELECT result FROM scan_log ORDER BY id DESC LIMIT 1").fetchone()[0] == "copy_added"

    def test_viewers_cannot(self, viewer_client, db):
        item_id = _insert_item(db, title="Dune", isbn="9780441013593")
        db.commit()
        assert viewer_client.post(f"/api/items/{item_id}/scan-copy").status_code == 403

    def test_an_unknown_item_is_an_error_card(self, editor_client):
        assert 'data-scan-status="error"' in editor_client.post("/api/items/99999/scan-copy").text


def test_browse_search_finds_a_game_by_its_barcode(admin_client, db):
    _insert_item(db, title="Murder", isbn=None, media_type="board_game", upc="5060756414005")
    db.commit()
    assert "Murder" in admin_client.get("/api/search", params={"q": "5060756414005"}).text


class TestStoreModeNonIsbn:
    def test_a_game_barcode_says_it_is_no_book_barcode(self, admin_client, db):
        result = admin_client.post("/api/store/queue", json={"isbns": ["4005556208166"]}).json()["results"][0]
        title = db.execute("SELECT title FROM items WHERE id = ?", (result["item_id"],)).fetchone()[0]
        assert title == "Not a book barcode (ISBN) — 4005556208166"

    def test_a_misread_isbn_is_still_unreadable(self, admin_client, db):
        result = admin_client.post("/api/store/queue", json={"isbns": ["9780441172710"]}).json()["results"][0]
        title = db.execute("SELECT title FROM items WHERE id = ?", (result["item_id"],)).fetchone()[0]
        assert title == "Unreadable barcode — 9780441172710"
