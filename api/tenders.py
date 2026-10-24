"""Tender persistence: upsert + dedupe on (portal, bid_no)."""
from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert

from agents.schemas import BidListing
from api.db import SessionLocal
from api.models import Tender

_KEEP_IF_NULL = ("item", "buyer", "quantity", "est_value", "doc_url")


def upsert_tenders(bids: list[BidListing]) -> tuple[list[int], list[int]]:
    """Insert or refresh tenders. Returns (all_ids, new_ids)."""
    if not bids:
        return [], []
    rows = {(b.portal, b.bid_no): b.model_dump() for b in bids}  # dedupe within the batch too
    stmt = insert(Tender).values(list(rows.values()))
    ex = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        constraint="uq_tender_portal_bid",
        set_={
            "title": ex.title,
            "end_date": ex.end_date,
            "source_url": ex.source_url,
            **{c: func.coalesce(getattr(ex, c), getattr(Tender, c)) for c in _KEEP_IF_NULL},
            "updated_at": func.now(),
        },
    ).returning(Tender.id, literal_column("(xmax = 0)").label("inserted"))
    with SessionLocal() as db:
        res = db.execute(stmt).all()
        db.commit()
    return [r.id for r in res], [r.id for r in res if r.inserted]


def tenders_missing_text(ids: list[int]) -> list[tuple[int, str]]:
    with SessionLocal() as db:
        q = select(Tender.id, Tender.doc_url).where(
            Tender.id.in_(ids), Tender.doc_text.is_(None), Tender.doc_url.is_not(None))
        return [(r.id, r.doc_url) for r in db.execute(q)]


def save_doc_text(tender_id: int, text: str) -> None:
    with SessionLocal() as db:
        t = db.get(Tender, tender_id)
        t.doc_text = text
        db.commit()
