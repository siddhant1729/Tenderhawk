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
async def sweep_portal(portal: str, category: str, emit: Callable[[StepEvent], None]) -> list[BidListing]
async def fetch_doc_text(url: str) -> str                      # TinyFish Fetch API
async def search_corrigenda(bid: BidListing) -> list[Corrigendum]  # TinyFish Search API
```
- In `FALLBACK_MODE`, `sweep_portal` replays `delivery/fixtures/{portal}_{category}.json`. It emits the recorded steps with 0.3–0.8 s delays.
- On validation failure: retry once, then log and skip that bid. One bad bid never fails a sweep.
- Captcha or blocked portal: emit an `error` event with `direct_url`, return `[]`. **Never bypass the captcha.**

### 3.3 SSE step event (Redis channel `sweep:{sweep_id}` → `GET /sweeps/{id}/events`)
```json
{"sweep_id": "uuid", "agent": "gem|cppp|haryana|corrigenda",
 "type": "started|step|result|error|done",
 "step": 7, "message": "Filtering by keyword 'steel rack'",
 "count": 12, "direct_url": null, "ts": "2026-10-24T14:02:11+05:30"}
```
`count` is set on `result` (bids found). `direct_url` is set on `error`. After all agents finish, one final event with `agent: "all"` and `type: "done"`.

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
| `POST` | `/matches/{id}/alert` | sends WhatsApp/email now (demo button) |
| `GET` | `/health` | `{db, redis, fallback_mode}` |

### 3.6 Matcher verdict rules (`api/matcher.py`)
Hard rules first, then rank by cosine similarity (profile embedding vs tender embedding).
- **NOT_ELIGIBLE** if any of these hold:
  - turnover is below `min_turnover`
  - the tender is limited to states the profile doesn't serve
  - the deadline has passed
- **CHECK_DOCS** if a criterion needs proof we can't verify: past experience, a certification not on the profile, or an unparseable value.
- **ELIGIBLE** otherwise.
- Every reason carries its `source_clause`. Example: `"Turnover met (₹1.2 Cr ≥ ₹50 L)"`, `"MSE EMD exemption applies"`.

### 3.7 Fixture format (`delivery/fixtures/{portal}_{category}.json`)
```json
{"portal": "gem", "category": "steel racks", "url": "...", "goal": "...",
 "recorded_at": "...", "run_id": "...",
 "steps": [{"step": 1, "message": "..."}],
 "result": [/* BidListing[] */],
 "docs": {"<doc_url>": "<fetched text>"}}
```

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
OPENAI_API_KEY=
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
ALERT_WHATSAPP_TO=
SMTP_URL=
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
| Siddhant | Record the first live GeM run → fixture (§3.7) | `delivery/fixtures/gem_steel_racks.json` exists |
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
| Vandan | Record fixtures: GeM, CPPP, Haryana × "steel racks" + doc texts | Full demo runs with wifi off |

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
- [ ] **Demo category keyword(s)** for Ramesh: "steel rack", "steel almirah", "fabricated structure"?
- [ ] **Stage phone** joined to the Twilio sandbox, and whose number
- [ ] **Run ports:** API `:8000`, web `:3000`, Postgres `:5432`, Redis `:6379`

## 8. Risks & fallbacks
| Risk | Fallback |
|---|---|
| Portal slow, down or captcha'd | Fixture replay (`FALLBACK_MODE`) and a direct link to the portal |
| Venue wifi dies | Everything local in Docker, plus fixtures; the WhatsApp alert needs a phone hotspot |
| LLM misparses a document | The verdict falls back to CHECK_DOCS with "Couldn't verify, see clause" |
| Agent returns malformed JSON | `output_schema` plus one retry, then skip |
| Running out of time | Cut order: email → corrigenda job → Haryana portal. **Never cut:** fallback mode, the clause highlight, WhatsApp |
