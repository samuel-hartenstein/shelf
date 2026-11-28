"""The not_found card's "Save anyway" and the duplicate card's "Another copy",
clicked in a real browser: both are htmx buttons whose request is assembled
from attributes (hx-vals, hx-include), which only a browser exercises."""

import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item
from tests.e2e.test_scan import _INJECT, _render_card, _watch_csp_violations

pytestmark = pytest.mark.e2e


def _query(live_server, sql, params=()):
    conn = sqlite3.connect(str(live_server["data_dir"] / "shelf.db"))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_save_anyway_files_the_code_tagged_unresolved(live_server, authed_page):
    csp = _watch_csp_violations(authed_page)
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")
    card = _render_card(status="not_found", isbn="9783849005351", media_type="book",
                        message="Not found — add manually below", preview_cover=None, locations=[])
    authed_page.evaluate(_INJECT, card)

    authed_page.get_by_test_id("save-unresolved").click()
    expect(authed_page.locator('#scan-results [data-scan-status="added"]')).to_have_count(1)
    rows = _query(live_server, "SELECT i.title, t.name FROM items i JOIN item_tags it ON it.item_id = i.id "
                               "JOIN tags t ON t.id = it.tag_id WHERE i.isbn = '9783849005351'")
    assert rows == [("9783849005351", "Unresolved")]
    assert csp == []


def test_another_copy_adds_a_second_copy(live_server, authed_page):
    item_id = insert_item(live_server["data_dir"], title="Feedback Duplicate", isbn="9780441013593")
    authed_page.goto(f"{live_server['url']}/scan")
    authed_page.wait_for_load_state("networkidle")
    card = _render_card(status="duplicate", isbn="9780441013593", title="Feedback Duplicate",
                        item_id=item_id, media_type="book")
    authed_page.evaluate(_INJECT, card)

    authed_page.get_by_test_id("scan-add-copy").click()
    expect(authed_page.locator('#scan-results [data-scan-status="copy_added"]')).to_have_count(1)
    assert _query(live_server, "SELECT COUNT(*) FROM item_copies WHERE item_id = ?", (item_id,)) == [(2,)]
