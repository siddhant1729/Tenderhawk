# TenderHawk — Build Plan (HackIIITD 2026)

> One page everyone works from. Context, scope and rules live in [`CLAUDE.md`](CLAUDE.md); this file says **who builds what, in what order, against which interfaces.**
> If you need to change an interface below, tell the owner and the people consuming it, then update this file.

---

## 0. Goal: the 3-minute demo

Everything we build serves this script. If a task doesn't help this demo, it waits.

1. Create **Ramesh's** profile: steel fabrication, 25 people, Faridabad, Udyam + GeM registered.
2. Click **Sweep now**. The live panel shows the GeM / CPPP / Haryana / corrigenda agents with step counts ticking.
3. Open a match to see the verdict, its reasons and the **highlighted source clause**.
4. A WhatsApp alert lands on a phone on stage.

**Must work offline:** `FALLBACK_MODE=true` replays recorded runs with the same UI and SSE events.

---

## 1. What we already know (sanity check, H0)

| Fact | Impact |
|---|---|
| TinyFish SDK `tinyfish==0.11.0`: `client.agent.run(url=, goal=, output_schema=)`, also `.stream()` and `.queue()` | Use `stream` for the live panel, `output_schema` to force our field names |
| One GeM run (first 3 bids) took **20 steps, ~150 s** | A live 3-portal sweep is too slow for the stage, so **fixtures are mandatory**, and live runs go in the background |
| The agent chose its own key names (`bid_number` instead of `bid_no`) | Always pass `output_schema`; validate with Pydantic |
| GeM date format: `24-10-2026 2:00 PM IST` | Shared date parser in `agents/schemas.py` |
| `.env` is gitignored, and the key loads via `python-dotenv` | Copy `.env.example` → `.env` and fill it in locally |
| GeM drops connections from non-Indian IPs (agent saw `ERR_EMPTY_RESPONSE`, "site down") | Agent runs use `ProxyConfig(country_code="IN")` |
| `output_schema` rejects `description` keys (HTTP 400) | Field hints live in the goal text (`agents/goals.py`) |
| GeM's search for "steel rack" returns no rack bids; buyers write "storage rack" / "Metal Shelving Racks" | Keyword choice decides whether a live sweep finds anything (see §7); goals match on items/title, never on buyer names |
| Fetch API → GeM bid docs gives `proxy_error` (Fetch has no proxy option), but Search API results include GeM `showbidDocument` text | No live doc text for GeM yet. For fixtures: hand-save bid PDFs and run `python -m delivery.build_fixture attach` (PDF → text → parse). Lasting fix (e.g. via Search) still open in `agents/` |
| One agent run with tooltip/detail clicks ran 10+ min | Runs capped at 30 steps / 300 s (`AGENT_LIMITS`); keep goals cheap |

---

## 2. Owners

| Area | Owner | Folder | Branch |
|---|---|---|---|
| TinyFish agents, schemas, SSE step events | **Siddhant** (lead) | `agents/` | `feat/agents` |
| FastAPI, DB models/migrations, eligibility parser, matcher | **Harsh** | `api/` | `feat/backend` |
| Next.js dashboard | **Aditya** | `web/` | `feat/web` |
| Alerts, seed data, fixtures, fallback, Docker | **Vandan** | `delivery/` | `feat/delivery` |

---

## 3. Interfaces (agree these first; build against them in parallel)

### 3.1 Agent output: `agents/schemas.py`
```python
class BidListing(BaseModel):
    portal: Literal["gem", "cppp", "haryana"]
    bid_no: str                     # unique together with portal
    title: str
    item: str | None = None
    buyer: str | None = None        # ministry / department
    quantity: str | None = None
    est_value: float | None = None  # INR
    end_date: datetime              # parsed, IST-aware
    doc_url: str | None = None
    source_url: str                 # link the user can open

class Corrigendum(BaseModel):
    bid_no: str
    summary: str                    # "Deadline extended to 02 Nov"
    new_end_date: datetime | None = None
    source_url: str
```

### 3.2 Agent functions: `agents/` (the only code that touches the web)
```python
async def sweep_portal(portal: str, category: str, emit: Callable[[StepEvent], None], sweep_id: str = "local") -> list[BidListing]
async def fetch_doc_text(url: str) -> str                      # TinyFish Fetch API
async def search_corrigenda(bid: BidListing) -> list[Corrigendum]  # TinyFish Search API
```
- `sweep_id` is stamped on every emitted `StepEvent`; the API passes the id it publishes to Redis.
- Fixture file names come from the keyword: `category.lower()`, non-alphanumerics → `_` (e.g. `"steel rack"` → `gem_steel_rack.json`).
- Every agent run goes through an Indian proxy and is capped at 30 steps / 300 s; a capped run ends as an `error` event with `direct_url`.
- In `FALLBACK_MODE`, `sweep_portal` replays `delivery/fixtures/{portal}_{category}.json`. It emits the recorded steps with 0.3–0.8 s delays.
- Unusable run result: retry the run once. Invalid bid: log and skip that bid. One bad bid never fails a sweep.
- Captcha or blocked portal: emit an `error` event with `direct_url`, return `[]`. **Never bypass the captcha.**

### 3.3 SSE step event (Redis channel `sweep:{sweep_id}` → `GET /sweeps/{id}/events`)
```json
{"sweep_id": "uuid", "agent": "gem|cppp|haryana|corrigenda",
 "type": "started|step|result|error|done",
 "step": 7, "message": "Filtering by keyword 'steel rack'",
 "count": 12, "direct_url": null, "ts": "2026-10-24T14:02:11+05:30"}
```
`count` is set on `result` (bids found). `direct_url` is set on `error`. After all agents finish, one final event with `agent: "all"` and `type: "done"`.
- Each portal card gets exactly one `started`, then `step`s, then one `result` or `error`, even though a portal is searched once per profile keyword (per-keyword messages are prefixed `[keyword]`). A portal ends in `error` (with `direct_url`) only when it found no bids and something failed (captcha, crash, no fixture).
- Pipeline progress after the portals finish is sent as `agent: "all"`, `type: "step"` (e.g. "Reading 10 bid documents", "Matching tenders against your profile"). The `done` message carries the summary.
- A client that connects late (or reconnects) gets the full event log replayed first. If a sweep goes silent for 15 min (server restarted mid-sweep), the stream ends with a `done` saying so.

### 3.4 Eligibility JSON (stored in `tenders.eligibility`)
```json
{
  "min_turnover":          {"value": 5000000, "source_clause": "Minimum average annual turnover ... Rs 50 Lakh"},
  "past_experience_years": {"value": 3,       "source_clause": "..."},
  "emd_amount":            {"value": 37000,   "source_clause": "..."},
  "emd_exemption_for_mse": {"value": true,    "source_clause": "..."},
  "mse_relaxation_experience_turnover":     {"value": false, "source_clause": "MSE Relaxation for Years of Experience and Turnover No"},
  "startup_relaxation_experience_turnover": {"value": false, "source_clause": "..."},
  "required_certifications": {"value": ["ISO 9001"], "source_clause": "..."},
  "documents_required":    {"value": ["Experience Criteria", "Bidder Turnover"], "source_clause": "..."},
  "states":                {"value": [],      "source_clause": null}
}
```
A criterion the document doesn't mention is stored as `{"value": null, "source_clause": null}` (list criteria: `{"value": [], "source_clause": null}`).
The two `*_relaxation_*` flags come straight from GeM bid documents: when MSE relaxation is `true`, an MSE is exempt from the turnover and experience criteria. `documents_required` feeds the document checklist.
`tenders.eligibility` stays `null` when the document couldn't be read or parsed; the matcher treats that as CHECK_DOCS.

### 3.5 REST API: FastAPI (`api/`)
| Method | Path | Body / returns |
|---|---|---|
| `POST` | `/profiles` | Profile JSON → `{id}` |
| `GET` | `/profiles/{id}` | Profile |
| `POST` | `/sweeps` | `{profile_id}` → `{sweep_id}` (runs in background) |
| `GET` | `/sweeps/{id}/events` | SSE stream of §3.3 events |
| `GET` | `/profiles/{id}/matches` | list: tender summary + verdict + fit_score, sorted |
| `GET` | `/matches/{id}` | tender, eligibility (§3.4), verdict, reasons, missing_docs, corrigenda |
| `POST` | `/matches/{id}/alert` | sends WhatsApp/email now (demo button) → `{sent, channels: {whatsapp, email}, preview}`; a channel without credentials reports `not_configured` |
| `GET` | `/health` | `{db, redis, fallback_mode, next_corrigenda_check}` |
| `POST` | `/profiles/{id}/match` | re-run the matcher on stored tenders without sweeping → verdict counts |
| `POST` | `/corrigenda/check` | run the daily corrigenda job now (Search API, no credits) → `{checked, new, extended}` |

### 3.6 Matcher verdict rules (`api/matcher.py`)
Hard rules first, then rank by cosine similarity (profile embedding vs tender embedding).
- **NOT_ELIGIBLE** if any of these hold:
  - turnover is below `min_turnover`
  - the tender is limited to states the profile doesn't serve
  - the deadline has passed
- **CHECK_DOCS** if a criterion needs proof we can't verify: past experience, a certification not on the profile, or an unparseable value.
- **ELIGIBLE** otherwise.
- Every reason carries its `source_clause`. Example: `"Turnover met (₹1.2 Cr ≥ ₹50 L)"`, `"MSE EMD exemption applies"`.
- Also CHECK_DOCS: no parse at all, or a parse that found no criteria (the fetched page may not be the bid document). Never show green without a clause.
- Past experience is never a hard fail: years in business ≠ years of past supply, so it is always "needs proof".
- MSE (Udyam) profiles skip turnover/experience when the bid's MSE relaxation is Yes, and skip EMD when the MSE EMD exemption applies.
- State names match loosely ("State of Haryana" = "Haryana", "UP" = "Uttar Pradesh").
- Tenders whose product fit is below a threshold are flagged `relevant: false` and listed last.

### 3.7 Fixture format (`delivery/fixtures/{portal}_{category}.json`)
```json
{"portal": "gem", "category": "steel racks", "url": "...", "goal": "...",
 "recorded_at": "...", "run_id": "...",
 "steps": [{"step": 1, "message": "..."}],
 "result": [/* BidListing[] */],
 "docs": {"<doc_url>": "<fetched text>"},
 "eligibility": {"<doc_url>": {/* §3.4 JSON */}}}
```
`eligibility` holds recorded parses, so a `FALLBACK_MODE` demo needs neither the LLM nor the network.

---

## 4. Environment (`.env.example`)
```
TINYFISH_API_KEY=
FALLBACK_MODE=false
DATABASE_URL=postgresql+psycopg://tenderhawk:tenderhawk@localhost:5432/tenderhawk
REDIS_URL=redis://localhost:6379/0
ANTHROPIC_API_KEY=
LLM_MODEL=claude-sonnet-5-5
LLM_MODEL_FAST=claude-haiku-5-5
LLM_EFFORT=medium            # effort for the eligibility parser
LLM_FALLBACKS=true           # server-side refusal fallback (Claude API only)
LIVE_MAX_KEYWORDS=1          # profile keywords per live sweep; each one is a billed Agent run per portal
SCHEDULER_ENABLED=true       # daily corrigenda job inside the API process (APScheduler)
CORRIGENDA_HOUR=7            # IST hour for the daily corrigenda job
OPENAI_API_KEY=
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
ALERT_WHATSAPP_TO=
SMTP_URL=                    # smtp://user:pass@host:587 (STARTTLS) or smtps://user:pass@host:465
ALERT_EMAIL_FROM=            # defaults to the SMTP user
ALERT_EMAIL_TO=              # overrides the profile's email (demo inbox)
AUTO_ALERT=true              # after each sweep, alert relevant open ELIGIBLE matches not alerted yet (max 3 per sweep, 20 s budget)
NEXT_PUBLIC_API_URL=http://localhost:8000
```
**Never commit `.env`.** Share secrets over DM, not in the repo or in chat logs.

### 4.1 Models (decided)
| Use | Model (env var) | Notes |
|---|---|---|
| Eligibility parser | `claude-sonnet-5-5` (`LLM_MODEL`) | Tool-use / JSON-schema structured output generated from the Pydantic `Eligibility` model (§3.4); every criterion carries `source_clause` |
| Light tasks (title normalising, alert copy) | `claude-haiku-5-5` (`LLM_MODEL_FAST`) | |
| Embeddings | `text-embedding-3-small` (`EMBEDDING_MODEL`, `EMBEDDING_DIM=1536`) | Tender = `title + item + buyer`; profile = `products + HSN descriptions`; cosine via pgvector |

- Model names live **only in env**. All LLM calls go through `api/llm.py`, all embedding calls through `api/embeddings.py`, so swapping a provider touches one file.

---

## 5. Timeline & tasks

Each block ends with a **checkpoint**: merge to `main`, and `main` must still run.

### H0–4 · Skeleton
| Who | Task | Done when |
|---|---|---|
| Siddhant | `agents/client.py` wrapper, `schemas.py` (§3.1), goals for GeM/CPPP/Haryana with `output_schema` | `python -m agents.cli gem "steel rack"` prints validated `BidListing`s |
| Siddhant | Record the first live GeM run → fixture (§3.7) | `delivery/fixtures/gem_steel_rack.json` exists with relevant, still-open bids |
| Harsh | FastAPI app, SQLAlchemy models (Tender/Profile/Match), Alembic init, `/health`, `/profiles` | `alembic upgrade head` + `curl /health` OK |
| Aditya | Next.js + Tailwind scaffold, layout, profile form (static) | Form renders, posts to a mock |
| Vandan | `docker-compose.yml`: Postgres 16 + pgvector, Redis; `.env.example`; Ramesh seed profile | `docker compose up` → both healthy |

### H4–10 · Data flowing
| Who | Task | Done when |
|---|---|---|
| Siddhant | `sweep_portal` with `agent.stream` → StepEvents; `fetch_doc_text`; CPPP + Haryana goals | All 3 portals return bids or a clean `error` event |
| Harsh | Upsert + dedupe on `(portal, bid_no)`; `POST /sweeps` background task; Redis pub → SSE endpoint | `curl -N /sweeps/{id}/events` streams steps |
| Aditya | Profile form wired to API; **Live agents panel** consuming SSE (4 cards: status, step count, last message, bids found) | Clicking Sweep animates the cards |
| Vandan | Fallback replay inside `sweep_portal` (with Siddhant); fixture recorder script | `FALLBACK_MODE=true` sweep looks identical in the UI |

### H10–16 · Intelligence  (**all fixtures recorded by H12**)
| Who | Task | Done when |
|---|---|---|
| Harsh | LLM eligibility parser → §3.4 JSON (structured output, Pydantic-validated); PyMuPDF for PDFs | 5 real GeM docs parse with clauses |
| Harsh | Matcher (§3.6) + pgvector embeddings + `/profiles/{id}/matches`, `/matches/{id}` | Ramesh gets ≥1 of each verdict colour |
| Siddhant | `search_corrigenda` + daily job (APScheduler); corrigenda agent card in SSE | A corrigendum shows on a match |
| Aditya | Matches list (verdict chips green/amber/red, value, deadline countdown) | Renders real API data |
| Vandan | Record fixtures: GeM, CPPP, Haryana × "storage rack" + doc texts + parses | Full demo runs with wifi off |

### H16–21 · Demo polish → tag `v0.1-demo`
| Who | Task | Done when |
|---|---|---|
| Vandan | Twilio sandbox WhatsApp alert + email; `/matches/{id}/alert`; auto-alert new ELIGIBLE matches | Message lands on the stage phone |
| Aditya | Match detail page: verdict, reasons, **highlighted source clause**, doc checklist, source link | Matches the demo script, step 3 |
| Harsh + Siddhant | Error states: portal blocked → "Open on portal" link; LLM failure → CHECK_DOCS | No blank screens or stack traces in UI |
| All | Full demo dry run, live and fallback | Tag `v0.1-demo` |

### H21–24 · Freeze
- **Fixes only.** No new features, no refactors.
- README with run steps, architecture diagram and the "TinyFish is the only thing touching the web" story.
- Rehearse the demo twice in each mode. Agree who presents and who drives the laptop.

---

## 6. How we work
- **Branches:** `feat/agents`, `feat/backend`, `feat/web`, `feat/delivery`. `main` must always be demo-able.
- **Commits:** small, every 30–60 min, using conventional prefixes (`feat(agents): ...`, `fix(api): ...`, `chore: ...`, `docs: ...`).
- **Budget:** Agent API runs cost credits, so use them only for listing sweeps and never per user. Use fixtures while developing the UI and matcher.
- **Rules that don't bend:**
  - Never bypass captchas.
  - Never submit bids (read-only).
  - Never commit secrets.
  - Only `agents/` touches the live web.

---

## 7. Open decisions (resolve in the first hour)
- [x] **LLM provider/model** → Anthropic: `claude-sonnet-5-5` (eligibility parser), `claude-haiku-5-5` (light tasks). See §4.1.
- [x] **Embedding model** → OpenAI `text-embedding-3-small`, 1536 dims (pgvector column `vector(1536)`). See §4.1.
- [x] **Demo category keyword(s)** for Ramesh → "storage rack", "steel rack", "steel almirah" (in `delivery/seed/ramesh.json`). "storage rack" comes first because a live GeM run for "steel rack" found no rack bids (§1). Live sweeps use only the first (`LIVE_MAX_KEYWORDS=1`); fixture replay uses all three, so record `gem_storage_rack.json` first.
- [ ] **Stage phone** joined to the Twilio sandbox, and whose number
- [x] **Run ports:** API `:8000`, web `:3000`, Postgres `:5432`, Redis `:6379`. Docker host ports can be moved with `API_PORT`, `WEB_PORT`, `POSTGRES_PORT`, `REDIS_PORT`.

## 8. Risks & fallbacks
| Risk | Fallback |
|---|---|
| Portal slow, down or captcha'd | Fixture replay (`FALLBACK_MODE`) and a direct link to the portal |
| Venue wifi dies | Everything local in Docker (`docker compose --profile app up -d`), plus fixtures with recorded parses (`FALLBACK_MODE=true`); the WhatsApp alert needs a phone hotspot |
| LLM misparses a document | The verdict falls back to CHECK_DOCS ("Couldn't read eligibility from the bid document"); a parse with no clauses is CHECK_DOCS too |
| Agent returns malformed JSON | `output_schema` plus one retry, then skip |
| Running out of time | Cut order: email → corrigenda job → Haryana portal. **Never cut:** fallback mode, the clause highlight, WhatsApp |

## 9. Progress (updated 2026-10-24)

Legend: ✅ done and verified · 🟡 partly done / blocked · ⬜ not started

**In short:** all code paths are built and merged to `main` (every `feat/*` branch is merged; nothing is left unmerged on a branch). What's left is mostly **keys, real fixtures and rehearsal**, not code.

All §3.5 endpoints exist. Docker: `docker compose --profile app up -d --build` runs the API (:8000) and dashboard (:3000) too.

### What's left (in priority order)
| # | Task | Owner | Blocked on |
|---|---|---|---|
| 1 | Get `ANTHROPIC_API_KEY` into `.env` (only `TINYFISH_API_KEY` is set) | Siddhant | key |
| 2 | Record real fixtures: `gem_storage_rack.json` first, then CPPP + Haryana, with doc texts and parses (`delivery.build_fixture attach`) and `check` = "fixture OK". The current `gem_steel_rack.json` is unusable (unrelated, closed bids) | Vandan + Siddhant | 1 |
| 3 | Run CPPP + Haryana live at least once (never run live yet); confirm the 30-step / 300 s cap ends cleanly | Siddhant | — |
| 4 | Run the eligibility parser on 5 real GeM docs, check the clauses | Harsh | 1, 2 |
| 5 | Twilio sandbox: `TWILIO_*` keys, stage phone joined (§7, still open), `ALERT_WHATSAPP_TO` set, send one real alert | Vandan | keys, phone |
| 6 | Optional: `OPENAI_API_KEY` for real embeddings (keyword-overlap ranking works without it); `SMTP_URL` for email (first thing to cut) | Harsh / Vandan | keys |
| 7 | Optional: corrigenda fixture `{portal}_corrigenda.json` so the 4th card shows a result offline | Siddhant | 2 |
| 8 | Full dry run live + fallback (checklist below), then tag `v0.1-demo` | All | 2, 5 |
| 9 | H21–24 freeze: fixes only, rehearse twice per mode, decide presenter / driver | All | 8 |

Open code issue (not blocking the demo): Fetch → GeM bid docs returns `proxy_error`, so live sweeps get no GeM doc text and those matches show CHECK_DOCS. Fixtures work around it with hand-saved PDFs.

| Block | Task | Owner | Status | Notes |
|---|---|---|---|---|
| H0–4 | TinyFish wrapper, schemas, goals, CLI | Siddhant | ✅ | merged to `main` |
| H0–4 | First live GeM fixture | Siddhant | 🟡 | `gem_steel_rack.json` exists, but its 10 bids are unrelated (matched "Ministry of Steel") and closed on 24 Oct, with no docs/parses. Re-record once the keyword is settled (§7). |
| H0–4 | FastAPI, models, Alembic, `/health`, `/profiles` | Harsh | ✅ | |
| H0–4 | `docker-compose.yml`, Ramesh seed | Vandan | ✅ | `python -m delivery.seed` |
| H0–4 | Next.js scaffold + profile form | Aditya (built by Harsh) | ✅ | `web/`, Next.js 16 + Tailwind 4; Ramesh demo fill button |
| H4–10 | Upsert + dedupe, `POST /sweeps`, Redis → SSE | Harsh | ✅ | late clients replay the full log |
| H4–10 | `sweep_portal` streaming, `fetch_doc_text`, CPPP + Haryana goals | Siddhant | 🟡 | GeM live run ✅ via Indian proxy (10 bids, 8 steps). **Fetch → GeM bid docs returns `proxy_error`**, so no doc text yet (§1). CPPP + Haryana not run live. Runs capped at 30 steps / 300 s (untested in a run). |
| H4–10 | Fallback replay | Vandan + Siddhant | ✅ | replays steps with delays; missing fixtures → error card with portal link |
| H4–10 | Live agents panel (SSE) | Aditya (built by Harsh) | ✅ | 4 cards from §3.3 events; survives reconnect replays; `?sweep=` follows a running sweep |
| H10–16 | LLM eligibility parser + PyMuPDF | Harsh | 🟡 | built and tested against a stub API; needs `ANTHROPIC_API_KEY` for the "5 real GeM docs" check |
| H10–16 | Matcher + embeddings + `/profiles/{id}/matches`, `/matches/{id}` | Harsh | ✅ | keyword-overlap ranking until `OPENAI_API_KEY` is set; clause excerpts for highlighting |
| H10–16 | `search_corrigenda` daily job + corrigenda card | Siddhant (backend wiring: Harsh) | ✅ | 4th SSE card in every sweep; daily 07:00 IST job + `POST /corrigenda/check`; extensions move `end_date`; shown as info reasons with links. Fallback reads `delivery/fixtures/{portal}_corrigenda.json` (`{bid_no: [Corrigendum]}`); none recorded yet |
| H10–16 | Matches list UI | Aditya (built by Harsh) | ✅ | verdict chips with text labels, value, deadline countdown, fit; low-relevance folded |
| H10–16 | Fixtures GeM/CPPP/Haryana × "storage rack" + doc texts + parses | Vandan | 🟡 | Tooling done: `python -m delivery.build_fixture attach/check`. Recording needs `TINYFISH_API_KEY` (live runs) and `ANTHROPIC_API_KEY` (parses) |
| H16–21 | Twilio WhatsApp + email, `/matches/{id}/alert` | Vandan (built by Harsh) | 🟡 | `delivery/alerts.py`: Twilio REST + SMTP, endpoint, auto-alert after sweeps; tested with stubs. Needs `TWILIO_*` + stage phone joined to the sandbox to send for real |
| H16–21 | Match detail page with highlighted clause | Aditya (built by Harsh) | ✅ | clause highlighted in the bid text, checklist, corrigenda, alert button with message preview |
| H16–21 | Error states (blocked portal link, LLM failure → CHECK_DOCS) | Harsh + Siddhant | ✅ | "Open on portal" cards, API-down / DB-down banners, reconnect notice, 404 pages |
| H21–24 | README, dry runs, tag `v0.1-demo` | All | 🟡 | README current. No tag yet. Dry runs and the tag wait for real fixtures and the Twilio sandbox (needs keys) |

Not built, by choice: OCR for scanned bid PDFs (CLAUDE.md stack). Every GeM bid PDF tested so far has a text layer; `build_fixture attach` reports a document with no text instead of guessing.

**Backend run steps (current):**
```
cp .env.example .env              # fill keys
docker compose up -d              # Postgres + Redis (skip redis if one already runs on :6379)
pip install -r requirements.txt
alembic upgrade head
python -m delivery.seed           # prints Ramesh's profile id
uvicorn api.main:app --reload     # :8000
python -m pytest -q tests         # 61 tests, no keys needed (2 alert tests need Postgres running)
cd web && npm test                # 14 tests
```

**Before every demo run (checklist):**
1. `docker compose up -d` (or the app profile), `alembic upgrade head`, `python -m delivery.seed`.
2. `python -m delivery.build_fixture check gem "storage rack"` (and cppp, haryana) prints "fixture OK" — bids still open, documents and parses recorded.
3. The stage phone has sent the sandbox join message to the Twilio number **within the last 24 h** (otherwise Twilio accepts the message but never delivers it). `ALERT_WHATSAPP_TO` is that phone.
4. `python -m delivery.alerts reset` so the on-stage sweep sends the alert again (rehearsals mark matches as alerted).
5. Fallback rehearsal with wifi off: `FALLBACK_MODE=true`, dashboard at :3000, sweep, open a match, send the alert over a phone hotspot.

