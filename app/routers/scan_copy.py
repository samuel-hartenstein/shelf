"""The duplicate scan card's "Another copy" button (scan → second copy).

Its own module: `item_copies.py` is at its size cap, and this route is a scan
outcome rather than copy editing. Writes go through `services.item_copies`.
"""

import logging
import sqlite3

from fastapi import APIRouter, Depends, Form, Request

from app import i18n
from app.auth import require_role
from app.database import get_db
from app.routers import items_common
from app.services import item_copies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.post("/items/{item_id}/scan-copy")
async def scan_add_copy(
    request: Request,
    item_id: int,
    location_id: str | None = Form(None),
    code: str | None = Form(None),
    mode: str | None = Form(None),
    _=Depends(require_role("editor")),
):
    """The duplicate scan card's "Another copy" button: one more physical copy
    of the item just scanned, at the scan page's location.

    A scan that finds the item already filed stays a duplicate by default (a
    second pass over the same shelf must not double the library); owning two
    is the one tap after it. An item with no copy row yet (it was filed
    without a location) first gets one for the copy already there, so the
    scan adds the second copy rather than relabelling the first.
    """
    templates = request.app.state.templates
    try:
        location = int(location_id) if location_id else None
    except ValueError:
        location = None
    error = None
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        item = db.execute(
            "SELECT id, title, authors, media_type, location_id, cover_path FROM items_live WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not item:
            db.rollback()
            error = i18n.gettext("Item not found")
        else:
            try:
                if not db.execute("SELECT 1 FROM copies_live WHERE item_id = ?", (item_id,)).fetchone():
                    item_copies.add_copy(db, item_id, {"location_id": item["location_id"]})
                copy_id = item_copies.add_copy(db, item_id, {"location_id": location})
            except (ValueError, sqlite3.IntegrityError) as exc:
                db.rollback()
                logger.warning("Scan copy add refused for item %s: %s", item_id, exc)
                error = i18n.gettext("Could not add that copy.")
            else:
                number = db.execute("SELECT copy_number FROM copies_live WHERE id = ?",
                                    (copy_id,)).fetchone()["copy_number"]
                place = None
                if location is not None:
                    row = db.execute("SELECT name FROM locations WHERE id = ?", (location,)).fetchone()
                    place = row["name"] if row else None
    if error:
        return templates.TemplateResponse(
            request, "fragments/scan_result.html",
            {"status": "error", "isbn": code or "", "message": error},
        )
    items_common._log_scan(code or "", item["media_type"], "copy_added", item_id, mode or "add")
    return templates.TemplateResponse(
        request, "fragments/scan_result.html",
        {
            "status": "copy_added", "isbn": code or "", "item_id": item_id,
            "title": item["title"], "authors": item["authors"],
            "cover_path": item["cover_path"], "media_type": item["media_type"],
            "message": i18n.gettext("Copy #%(n)s · %(place)s")
            % {"n": number, "place": place or i18n.gettext("no location")},
        },
    )
