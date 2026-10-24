"""Sweep orchestration: portals x profile keywords -> tenders in Postgres, events on Redis.

Each portal card in the UI gets one started -> steps -> result|error sequence, even though a portal
is searched once per keyword: per-keyword started/result/error events are folded into steps.
"""
import asyncio
import json
import logging
from functools import lru_cache

from agents.client import FIXTURES_DIR, fetch_doc_text, sweep_portal
from agents.goals import PORTALS
from agents.schemas import BidListing, StepEvent
from api.config import fallback_mode
from api.db import SessionLocal
from api.documents import to_text
from api.eligibility import parse_eligibility
from api.events import publish
from api.llm import LLMError
from api.models import Profile
from api.tenders import (save_doc_text, save_eligibility, tenders_missing_eligibility, tenders_missing_text,
                         upsert_tenders)

log = logging.getLogger("api.sweeps")
DOC_FETCH_CONCURRENCY = 4
LLM_CONCURRENCY = 4


def _keywords(profile: Profile) -> list[str]:
    return [k for k in (profile.keywords or profile.products[:2]) if k.strip()]


async def _sweep_portal_all_keywords(sweep_id: str, portal: str, keywords: list[str]) -> list[BidListing]:
    step = 0

    def ev(type_: str, message: str, **kw) -> None:
        publish(StepEvent(sweep_id=sweep_id, agent=portal, type=type_, step=step, message=message, **kw))

    def relay(e: StepEvent) -> None:
        nonlocal step
        step += 1
        if e.type == "result":
            msg = f"Found {e.count} bids"
        else:
            msg = e.message
        ev("step", f"[{kw}] {msg}")

    ev("started", f"Searching {PORTALS[portal]['url']} for {', '.join(keywords)}")
    bids: dict[str, BidListing] = {}
    errors: list[str] = []
    for kw in keywords:
        before = len(errors)

        def relay_kw(e: StepEvent) -> None:
            if e.type == "error":
                errors.append(e.message)
            relay(e)

        for b in await sweep_portal(portal, kw, relay_kw, sweep_id):
            bids.setdefault(b.bid_no, b)
        if len(errors) > before and "blocked" in errors[-1].lower():
            break  # captcha / login wall: don't hammer the portal with the other keywords

    if not bids and errors and len(errors) == len(keywords):
        ev("error", errors[0], direct_url=PORTALS[portal]["url"])
        return []
    ev("result", f"Found {len(bids)} bids", count=len(bids))
    return list(bids.values())


async def _fetch_docs(sweep_id: str, tender_ids: list[int]) -> int:
    todo = await asyncio.to_thread(tenders_missing_text, tender_ids)
    if not todo:
        return 0
    publish(StepEvent(sweep_id=sweep_id, agent="all", type="step", message=f"Reading {len(todo)} bid documents"))
    sem = asyncio.Semaphore(DOC_FETCH_CONCURRENCY)

    async def one(tid: int, url: str) -> bool:
        async with sem:
            text = await fetch_doc_text(url)
        text = to_text(text) if text else ""
        if text:
            await asyncio.to_thread(save_doc_text, tid, text)
        return bool(text)

    return sum(await asyncio.gather(*(one(t, u) for t, u in todo)))


@lru_cache(maxsize=1)
def _fixture_eligibility() -> dict[str, dict]:
    """Recorded parses ({doc_url: eligibility}) so FALLBACK_MODE works without the LLM."""
    out: dict[str, dict] = {}
    for p in FIXTURES_DIR.glob("*.json"):
        data = json.loads(p.read_text())
        if isinstance(data, dict):
            out.update(data.get("eligibility", {}))
    return out


async def _parse_docs(sweep_id: str, tender_ids: list[int]) -> int:
    """Eligibility for every tender that has text but no parse yet. Failures leave it null (-> CHECK_DOCS)."""
    todo = await asyncio.to_thread(tenders_missing_eligibility, tender_ids)
    if not todo:
        return 0
    publish(StepEvent(sweep_id=sweep_id, agent="all", type="step",
                      message=f"Reading eligibility criteria from {len(todo)} documents"))
    sem = asyncio.Semaphore(LLM_CONCURRENCY)

    async def one(tid: int, doc_url: str | None, text: str) -> bool:
        el = _fixture_eligibility().get(doc_url or "") if fallback_mode() else None
        if el is None:
            try:
                async with sem:
                    el = await parse_eligibility(text)
            except LLMError as e:
                log.warning("eligibility parse failed for tender %s: %s", tid, e)
                return False
        await asyncio.to_thread(save_eligibility, tid, el)
        return True

    return sum(await asyncio.gather(*(one(*row) for row in todo)))


async def run_sweep(sweep_id: str, profile_id: int) -> None:
    """Background task for POST /sweeps. Never raises; always ends with an all/done event."""
    summary = "Sweep failed"
    try:
        with SessionLocal() as db:
            profile = db.get(Profile, profile_id)
            keywords = _keywords(profile)
        if not keywords:
            publish(StepEvent(sweep_id=sweep_id, agent="all", type="error",
                              message="Profile has no keywords or products to search for"))
            return
        per_portal = await asyncio.gather(
            *(_sweep_portal_all_keywords(sweep_id, p, keywords) for p in PORTALS))
        bids = [b for bs in per_portal for b in bs]
        all_ids, new_ids = await asyncio.to_thread(upsert_tenders, bids)
        read = await _fetch_docs(sweep_id, all_ids)
        parsed = await _parse_docs(sweep_id, all_ids)
        summary = f"{len(all_ids)} tenders ({len(new_ids)} new), {read} documents read, {parsed} parsed"
    except Exception as e:
        log.exception("sweep %s failed", sweep_id)
        publish(StepEvent(sweep_id=sweep_id, agent="all", type="error", message=f"Sweep failed: {e}"))
    finally:
        publish(StepEvent(sweep_id=sweep_id, agent="all", type="done", message=summary))
