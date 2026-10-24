"""TinyFish wrapper. The only module in the repo that touches the live web (PLAN.md §3.2).

TinyFish navigates every portal. Bid PDFs it finds that TinyFish Fetch can't read (GeM: Fetch has no Indian
proxy) are downloaded directly over the Indian connection, here and nowhere else.
"""
import asyncio
import json
import logging
import os
import random
import re
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError
from tinyfish import AgentConfig, AsyncTinyFish, CompleteEvent, ProgressEvent, ProxyConfig, StartedEvent

from agents.goals import PORTALS, goal_for
from agents.schemas import AGENT_OUTPUT_SCHEMA, BidListing, Corrigendum, StepEvent
from api.documents import to_text

load_dotenv()
log = logging.getLogger("agents")

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "delivery" / "fixtures"

Emit = Callable[[StepEvent], None]

# Indian govt portals drop connections from non-Indian IPs (GeM: ERR_EMPTY_RESPONSE without this).
INDIA_PROXY = ProxyConfig(enabled=True, country_code="IN")

# Hard cap so a looping agent becomes a clean error card instead of a sweep that never ends (and keeps billing).
# Time only: a custom max_steps is rejected (HTTP 403 "not enabled for custom max steps", beta-only).
AGENT_LIMITS = AgentConfig(max_duration_seconds=300)

# Direct downloads: only bid documents on the portals we sweep, only over HTTPS, only PDFs of sane size.
DOC_HOSTS = {urlparse(p["url"]).hostname for p in PORTALS.values()}
DOC_MAX_BYTES = 15_000_000

_client: AsyncTinyFish | None = None


def fallback_mode() -> bool:
    return os.getenv("FALLBACK_MODE", "false").lower() in ("1", "true", "yes")


def _tf() -> AsyncTinyFish:
    global _client
    if _client is None:
        _client = AsyncTinyFish()  # reads TINYFISH_API_KEY
    return _client


def fixture_path(portal: str, category: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")
    return FIXTURES_DIR / f"{portal}_{slug}.json"


def to_listings(portal: str, raw_bids: list[dict]) -> list[BidListing]:
    """Validate raw agent rows. A bad row is logged and skipped, never fails the sweep."""
    base = PORTALS[portal]["url"]
    out: dict[str, BidListing] = {}
    for raw in raw_bids:
        row = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        row = {k: v for k, v in row.items() if v not in ("", None)}
        if row.get("doc_url"):
            row["doc_url"] = urljoin(base, row["doc_url"])
        row.setdefault("source_url", row.get("doc_url") or base)
        try:
            bid = BidListing(portal=portal, **row)
        except ValidationError as e:
            log.warning("skip %s bid %r: %s", portal, raw.get("bid_no"), e.errors()[0]["msg"])
            continue
        out.setdefault(bid.bid_no, bid)
    return list(out.values())


async def run_agent(portal: str, category: str, on_step: Callable[[int, str], None]) -> dict:
    """One live agent run, retried once if the result is unusable.

    Returns {"run_id", "url", "goal", "steps", "result"} where result is the raw agent JSON.
    """
    url, goal = goal_for(portal, category)
    last_error = "no result"
    for attempt in (1, 2):
        steps: list[dict] = []
        run_id, result = None, None
        async with _tf().agent.stream(
            url=url, goal=goal, output_schema=AGENT_OUTPUT_SCHEMA, proxy_config=INDIA_PROXY,
            agent_config=AGENT_LIMITS,
        ) as stream:
            async for ev in stream:
                if isinstance(ev, StartedEvent):
                    run_id = ev.run_id
                elif isinstance(ev, ProgressEvent):
                    steps.append({"step": len(steps) + 1, "message": ev.purpose})
                    on_step(len(steps), ev.purpose)
                elif isinstance(ev, CompleteEvent):
                    if ev.status == "COMPLETED" and isinstance(ev.result_json, dict):
                        result = ev.result_json
                    else:
                        last_error = ev.error.message if ev.error else f"run {ev.status}"
        if result is not None and (result.get("blocked") or isinstance(result.get("bids"), list)):
            return {"run_id": run_id, "url": url, "goal": goal, "steps": steps, "result": result}
        log.warning("%s run %s unusable (attempt %d): %s", portal, run_id, attempt, last_error)
    raise RuntimeError(f"{portal} agent failed: {last_error}")


async def _replay(portal: str, category: str, emit: Emit, sweep_id: str) -> list[BidListing]:
    path = fixture_path(portal, category)
    if not path.exists():
        emit(StepEvent(sweep_id=sweep_id, agent=portal, type="error",
                       message=f"No recorded run for '{category}'", direct_url=PORTALS[portal]["url"]))
        return []
    fx = json.loads(path.read_text())
    emit(StepEvent(sweep_id=sweep_id, agent=portal, type="started", message=f"Opening {fx['url']}"))
    for s in fx["steps"]:
        await asyncio.sleep(random.uniform(0.3, 0.8))
        emit(StepEvent(sweep_id=sweep_id, agent=portal, type="step", step=s["step"], message=s["message"]))
    bids = [BidListing.model_validate(b) for b in fx["result"]]
    emit(StepEvent(sweep_id=sweep_id, agent=portal, type="result", step=len(fx["steps"]),
                   message=f"Found {len(bids)} bids", count=len(bids)))
    return bids


async def sweep_portal(portal: str, category: str, emit: Emit, sweep_id: str = "local") -> list[BidListing]:
    """Sweep one portal for one category, streaming StepEvents. Never raises; errors become events."""
    if fallback_mode():
        return await _replay(portal, category, emit, sweep_id)

    url = PORTALS[portal]["url"]
    emit(StepEvent(sweep_id=sweep_id, agent=portal, type="started", message=f"Opening {url}"))

    def on_step(n: int, msg: str) -> None:
        emit(StepEvent(sweep_id=sweep_id, agent=portal, type="step", step=n, message=msg))

    try:
        run = await run_agent(portal, category, on_step)
    except Exception as e:  # network, auth, run failure: surface a direct link instead
        log.exception("%s sweep failed", portal)
        emit(StepEvent(sweep_id=sweep_id, agent=portal, type="error", message=str(e), direct_url=url))
        return []

    result = run["result"]
    if result.get("blocked"):
        emit(StepEvent(sweep_id=sweep_id, agent=portal, type="error", step=len(run["steps"]),
                       message=f"Blocked: {result.get('blocked_reason') or 'captcha'}. Open on portal.",
                       direct_url=url))
        return []

    bids = to_listings(portal, result.get("bids", []))
    emit(StepEvent(sweep_id=sweep_id, agent=portal, type="result", step=len(run["steps"]),
                   message=f"Found {len(bids)} bids", count=len(bids)))
    return bids


def _fixture_docs() -> dict[str, str]:
    docs: dict[str, str] = {}
    for p in FIXTURES_DIR.glob("*.json"):
        docs.update(json.loads(p.read_text()).get("docs", {}))
    return docs


async def _fetch(url: str) -> str:
    try:
        resp = await _tf().fetch.get_contents([url], format="markdown")
    except Exception:
        log.exception("fetch failed: %s", url)
        return ""
    if resp.errors or not resp.results:
        log.warning("fetch returned no text for %s: %s", url, resp.errors)
        return ""
    return to_text(resp.results[0].text or "")


async def _direct(url: str) -> str:
    """Last resort for a bid PDF on a swept portal. A TinyFish agent can't read these (the browser downloads
    the PDF instead of showing it: 20 steps, no text), so fetch the file itself over the Indian connection."""
    u = urlparse(url)
    if u.scheme != "https" or u.hostname not in DOC_HOSTS:
        return ""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
            r = await http.get(url)
    except httpx.HTTPError as e:
        log.warning("direct download failed for %s: %r", url, e)
        return ""
    if r.status_code != 200 or len(r.content) > DOC_MAX_BYTES or r.content[:5] != b"%PDF-":
        log.warning("direct download of %s: HTTP %s, %d bytes, not a usable PDF", url, r.status_code, len(r.content))
        return ""
    return to_text(r.content)


async def fetch_doc_text(url: str) -> tuple[str, str]:
    """Bid page/document -> (clean text, source). source: fetch | direct | fixture, or "" with no text."""
    if fallback_mode():
        text = _fixture_docs().get(url, "")
        return (text, "fixture") if text else ("", "")
    for source, get in (("fetch", _fetch), ("direct", _direct)):
        text = await get(url)
        if text.strip():
            log.info("doc text for %s: %d chars, source=%s", url, len(text), source)
            return text, source
    return "", ""


async def search_corrigenda(bid: BidListing) -> list[Corrigendum]:
    """Look for corrigenda / deadline extensions for a bid via TinyFish Search."""
    if fallback_mode():
        fx = fixture_path(bid.portal, "corrigenda")
        rows = json.loads(fx.read_text()).get(bid.bid_no, []) if fx.exists() else []
        return [Corrigendum.model_validate(r) for r in rows]
    try:
        resp = await _tf().search.query(f'"{bid.bid_no}" corrigendum OR extension', location="IN")
    except Exception:
        log.exception("search failed: %s", bid.bid_no)
        return []
    out = []
    for r in resp.results:
        text = f"{r.title} {r.snippet}"
        if bid.bid_no.lower() in text.lower() and re.search(r"corrigend|extend|extension|amend", text, re.I):
            out.append(Corrigendum(bid_no=bid.bid_no, summary=r.snippet or r.title, source_url=r.url))
    return out
