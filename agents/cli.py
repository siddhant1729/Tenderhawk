"""Run one portal sweep from the terminal.

    python -m agents.cli gem "steel rack"            # live (costs credits) or replay if FALLBACK_MODE=true
    python -m agents.cli gem "steel rack" --record   # live run, saved to delivery/fixtures/ with doc texts
"""
import argparse
import asyncio
import json
import logging
from datetime import datetime

from agents.client import FIXTURES_DIR, fetch_doc_text, fixture_path, run_agent, sweep_portal, to_listings
from agents.goals import PORTALS
from agents.schemas import IST, StepEvent


def print_event(ev: StepEvent) -> None:
    extra = f" ({ev.count})" if ev.count is not None else f" -> {ev.direct_url}" if ev.direct_url else ""
    print(f"[{ev.agent}:{ev.type}:{ev.step}] {ev.message}{extra}", flush=True)


async def record(portal: str, category: str) -> None:
    run = await run_agent(portal, category, lambda n, m: print(f"[{portal}:step:{n}] {m}", flush=True))
    bids = to_listings(portal, run["result"].get("bids", []))
    if not bids:
        r = run["result"]
        print(f"not saved: 0 bids" + (f" (blocked: {r.get('blocked_reason')})" if r.get("blocked") else " (no relevant bids)"))
        return
    docs = {}
    for b in bids:
        if b.doc_url:
            text = await fetch_doc_text(b.doc_url)
            print(f"fetched {len(text)} chars: {b.doc_url}", flush=True)
            if text:
                docs[b.doc_url] = text
    fx = {
        "portal": portal, "category": category, "url": run["url"], "goal": run["goal"],
        "recorded_at": datetime.now(IST).isoformat(), "run_id": run["run_id"],
        "steps": run["steps"], "raw_result": run["result"],
        "result": [b.model_dump(mode="json") for b in bids], "docs": docs,
    }
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = fixture_path(portal, category)
    path.write_text(json.dumps(fx, indent=2, ensure_ascii=False))
    print(f"saved {len(bids)} bids, {len(run['steps'])} steps -> {path}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("portal", choices=list(PORTALS))
    ap.add_argument("category")
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.record:
        await record(args.portal, args.category)
        return
    bids = await sweep_portal(args.portal, args.category, print_event)
    for b in bids:
        print(b.model_dump_json())


if __name__ == "__main__":
    asyncio.run(main())
