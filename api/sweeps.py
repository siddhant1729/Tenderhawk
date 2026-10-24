"""Sweep orchestration: portals x profile keywords -> tenders in Postgres, events on Redis.

Each portal card in the UI gets one started -> steps -> result|error sequence, even though a portal
is searched once per keyword: per-keyword started/result/error events are folded into steps.
Nothing in here may raise out of run_sweep: one bad portal or document never sinks the sweep.
"""
import asyncio
import json
import logging
import os

from agents.client import FIXTURES_DIR, fetch_doc_text, sweep_portal
from agents.goals import PORTALS
from agents.schemas import BidListing, StepEvent
from api.config import fallback_mode
from api.corrigenda import card_emitter, check_corrigenda
from api.db import SessionLocal
from api.documents import to_text
from api.eligibility import parse_eligibility
from api.events import publish
from api.matching import rematch
from api.models import Profile
from delivery.alerts import auto_alert
from api.tenders import (save_doc_text, save_eligibility, tenders_missing_eligibility, tenders_missing_text,
                         upsert_tenders)

log = logging.getLogger("api.sweeps")
DOC_FETCH_CONCURRENCY = 4
LLM_CONCURRENCY = 4
DOC_FETCH_TIMEOUT = 90     # seconds per document
PARSE_TIMEOUT = 240        # seconds per document (LLM)
# Each live keyword is one billed TinyFish Agent run per portal (~150 s). Fixture replay is free.
LIVE_MAX_KEYWORDS = int(os.getenv("LIVE_MAX_KEYWORDS", "1"))


def _keywords(profile: Profile) -> list[str]:
    kws = [k.strip() for k in (profile.keywords or profile.products[:2]) if k and k.strip()]
    return kws if fallback_mode() else kws[:LIVE_MAX_KEYWORDS]


def _load_keywords(profile_id: int) -> list[str]:
    with SessionLocal() as db:
        return _keywords(db.get(Profile, profile_id))


async def _sweep_portal_all_keywords(sweep_id: str, portal: str, keywords: list[str]) -> list[BidListing]:
    step = 0
    direct_url = PORTALS[portal]["url"]

    def ev(type_: str, message: str, **kw) -> None:
        publish(StepEvent(sweep_id=sweep_id, agent=portal, type=type_, step=step, message=message, **kw))

    ev("started", f"Searching {direct_url} for {', '.join(keywords)}")
    bids: dict[str, BidListing] = {}
    errors: list[str] = []
    try:
        for kw in keywords:
            before = len(errors)

            def relay(e: StepEvent, kw: str = kw) -> None:
                nonlocal step
                step += 1
                if e.type == "error":
                    errors.append(e.message)
                ev("step", f"[{kw}] " + (f"Found {e.count} bids" if e.type == "result" else e.message))

            for b in await sweep_portal(portal, kw, relay, sweep_id):
                bids.setdefault(b.bid_no, b)
            if len(errors) > before and "blocked" in errors[-1].lower():
                break  # captcha / login wall: don't hammer the portal with the other keywords
    except Exception as e:  # bad fixture row, Redis hiccup...: this card fails, the sweep goes on
        log.exception("%s portal sweep crashed", portal)
        errors.append(f"{portal} sweep failed: {e}")

    if not bids and errors:
        ev("error", errors[-1], direct_url=direct_url)
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
        try:
            async with sem:
                raw = await asyncio.wait_for(fetch_doc_text(url), DOC_FETCH_TIMEOUT)
            text = to_text(raw) if raw else ""
            if text:
                await asyncio.to_thread(save_doc_text, tid, text)
            return bool(text)
        except Exception as e:
            log.warning("document fetch/save failed for tender %s (%s): %r", tid, url, e)
            return False

    return sum(await asyncio.gather(*(one(t, u) for t, u in todo)))


def _fixture_eligibility() -> dict[str, dict]:
    """Recorded parses ({doc_url: eligibility}) so FALLBACK_MODE works without the LLM."""
    out: dict[str, dict] = {}
    for p in FIXTURES_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            log.warning("unreadable fixture %s", p)
            continue
        if isinstance(data, dict):
            out.update(data.get("eligibility") or {})
    return out


async def _parse_docs(sweep_id: str, tender_ids: list[int]) -> int:
    """Eligibility for every tender that has text but no parse yet. Failures leave it null (-> CHECK_DOCS)."""
    todo = await asyncio.to_thread(tenders_missing_eligibility, tender_ids)
    if not todo:
        return 0
    publish(StepEvent(sweep_id=sweep_id, agent="all", type="step",
                      message=f"Reading eligibility criteria from {len(todo)} documents"))
    recorded = await asyncio.to_thread(_fixture_eligibility) if fallback_mode() else {}
    sem = asyncio.Semaphore(LLM_CONCURRENCY)

    async def one(tid: int, doc_url: str | None, text: str) -> bool:
        try:
            el = recorded.get(doc_url or "")
            if el is None:
                async with sem:
                    el = await asyncio.wait_for(parse_eligibility(text), PARSE_TIMEOUT)
            await asyncio.to_thread(save_eligibility, tid, el)
            return True
        except Exception as e:  # LLMError, timeout, DB error: this tender stays unparsed -> CHECK_DOCS
            log.warning("eligibility parse failed for tender %s: %r", tid, e)
            return False

    return sum(await asyncio.gather(*(one(*row) for row in todo)))


async def _auto_alert(sweep_id: str, profile_id: int) -> int:
    try:
        sent = await auto_alert(profile_id)
    except Exception:
        log.exception("auto-alert failed")
        return 0
    if sent:
        publish(StepEvent(sweep_id=sweep_id, agent="all", type="step",
                          message=f"Sent {sent} WhatsApp/email alert{'s' if sent != 1 else ''} for new eligible tenders"))
    return sent


async def run_sweep(sweep_id: str, profile_id: int) -> None:
    """Background task for POST /sweeps. Never raises; always ends with exactly one all/done event."""
    summary = "Sweep failed"
    try:
        keywords = await asyncio.to_thread(_load_keywords, profile_id)
        if not keywords:
            publish(StepEvent(sweep_id=sweep_id, agent="all", type="error",
                              message="Profile has no keywords or products to search for"))
            summary = "Nothing to search for"
            return
        corr_card = card_emitter(sweep_id, publish)
        corr_card("started", "Waiting for bids from the portals", None)
        per_portal = await asyncio.gather(
            *(_sweep_portal_all_keywords(sweep_id, p, keywords) for p in PORTALS))
        bids = [b for bs in per_portal for b in bs]
        all_ids, new_ids = await asyncio.to_thread(upsert_tenders, bids)

        async def documents() -> tuple[int, int]:
            return await _fetch_docs(sweep_id, all_ids), await _parse_docs(sweep_id, all_ids)

        async def corrigenda() -> dict:
            try:
                return await check_corrigenda(all_ids, corr_card)
            except Exception as e:
                log.exception("corrigenda check failed")
                corr_card("error", f"Corrigenda check failed: {e}", None)
                return {"new": 0}

        (read, parsed), corr = await asyncio.gather(documents(), corrigenda())
        publish(StepEvent(sweep_id=sweep_id, agent="all", type="step",
                          message="Matching tenders against your profile"))
        m = await rematch(profile_id)
        c = m["counts"]
        alerts = await _auto_alert(sweep_id, profile_id)
        low = m["total"] - m["relevant"]
        summary = (f"{len(all_ids)} tenders found ({len(new_ids)} new), {read} documents read, {parsed} parsed. "
                   f"{c.get('ELIGIBLE', 0)} eligible, {c.get('CHECK_DOCS', 0)} check docs, "
                   f"{c.get('NOT_ELIGIBLE', 0)} not eligible"
                   + (f", {low} low-relevance hidden" if low else "")
                   + (f". {corr['new']} new corrigenda" if corr.get("new") else "")
                   + (f". {alerts} alert{'s' if alerts != 1 else ''} sent" if alerts else ""))
    except Exception as e:
        log.exception("sweep %s failed", sweep_id)
        publish(StepEvent(sweep_id=sweep_id, agent="all", type="error", message=f"Sweep failed: {e}"))
    finally:
        publish(StepEvent(sweep_id=sweep_id, agent="all", type="done", message=summary))
