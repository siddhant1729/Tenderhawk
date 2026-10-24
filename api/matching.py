"""Run the matcher for a profile against every stored tender and upsert matches."""
import asyncio
from collections import Counter

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert

from api import embeddings
from api.db import SessionLocal
from api.matcher import MIN_FIT_COSINE, MIN_FIT_KEYWORD, ProfileFacts, evaluate, keyword_fit
from api.models import Match, Profile, Tender


def _missing_embeddings(profile_id: int, tender_ids: list[int]) -> list[tuple[type, int, str]]:
    with SessionLocal() as db:
        p = db.get(Profile, profile_id)
        todo = [(Profile, p.id, embeddings.profile_text(p.products, p.keywords, p.hsn_codes))] \
            if p.embedding is None else []
        for t in db.scalars(select(Tender).where(Tender.id.in_(tender_ids), Tender.embedding.is_(None))):
            todo.append((Tender, t.id, embeddings.tender_text(t.title, t.item, t.buyer)))
        return todo


def _save_embeddings(rows: list[tuple[type, int, list[float]]]) -> None:
    with SessionLocal() as db:
        for model, oid, vec in rows:
            db.get(model, oid).embedding = vec
        db.commit()


async def ensure_embeddings(profile_id: int, tender_ids: list[int]) -> int:
    """Embed the profile and any tenders that lack an embedding. No-op without OPENAI_API_KEY."""
    if not embeddings.enabled():
        return 0
    todo = await asyncio.to_thread(_missing_embeddings, profile_id, tender_ids)
    if not todo:
        return 0
    vecs = await embeddings.embed([text for _, _, text in todo])  # no DB session held while waiting
    if vecs is None:
        return 0
    await asyncio.to_thread(_save_embeddings, [(m, i, v) for (m, i, _), v in zip(todo, vecs)])
    return len(todo)


def match_profile(profile_id: int) -> dict:
    """Evaluate every tender for this profile. Returns verdict counts and ids of newly created matches."""
    with SessionLocal() as db:
        p = db.get(Profile, profile_id)
        facts = ProfileFacts(p.annual_turnover, p.years_in_business, p.certifications or [], p.states_served or [])
        tenders = db.scalars(select(Tender)).all()
        if not tenders:
            return {"counts": {}, "new_match_ids": []}

        use_cosine = p.embedding is not None and all(t.embedding is not None for t in tenders)
        if use_cosine:
            rows = db.execute(select(Tender.id, (1 - Tender.embedding.cosine_distance(p.embedding)).label("fit")))
            fits = {r.id: round(float(r.fit), 3) for r in rows}
            threshold = MIN_FIT_COSINE
        else:
            phrases = (p.keywords or []) + (p.products or [])
            fits = {t.id: keyword_fit(phrases, t.title, t.item) for t in tenders}
            threshold = MIN_FIT_KEYWORD

        values = []
        for t in tenders:
            r = evaluate(facts, t.end_date, t.eligibility, corrigenda=t.corrigenda)
            values.append({"profile_id": p.id, "tender_id": t.id, "verdict": r.verdict, "reasons": r.reasons,
                           "missing_docs": r.missing_docs, "fit_score": fits[t.id],
                           "relevant": fits[t.id] >= threshold})
        stmt = insert(Match).values(values)
        ex = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            constraint="uq_match_profile_tender",
            set_={"verdict": ex.verdict, "reasons": ex.reasons, "missing_docs": ex.missing_docs,
                  "fit_score": ex.fit_score, "relevant": ex.relevant, "updated_at": func.now()},
        ).returning(Match.id, literal_column("(xmax = 0)").label("inserted"))
        res = db.execute(stmt).all()
        db.commit()
    counts = Counter(v["verdict"] for v in values if v["relevant"])
    return {"counts": dict(counts), "relevant": sum(v["relevant"] for v in values), "total": len(values),
            "method": "embeddings" if use_cosine else "keywords",
            "new_match_ids": [r.id for r in res if r.inserted]}


def _all_tender_ids() -> list[int]:
    with SessionLocal() as db:
        return list(db.scalars(select(Tender.id)))


async def rematch(profile_id: int, tender_ids: list[int] | None = None) -> dict:
    if tender_ids is None:
        tender_ids = await asyncio.to_thread(_all_tender_ids)
    await ensure_embeddings(profile_id, tender_ids)
    return await asyncio.to_thread(match_profile, profile_id)
