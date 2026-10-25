# Maître D: working on this codebase

A voice agent that takes table bookings for Saffron Table, a fictional London restaurant. It runs
on the ElevenLabs Agents Platform. A FastAPI backend owns the booking domain and exposes it to the
agent as server tools (webhooks). Postgres stores bookings and calls. A React dashboard shows
bookings, calls and a per-call timeline, and can start a web voice call.

`docs/plan.md` is the design: architecture, data model, tool contract, milestones.
`docs/decisions.md` records why things are the way they are. Read both before changing anything
structural.

> **Status: M0.** Only the plan exists. The layout and commands below describe what the
> milestones build. Update this file when a milestone changes what is true.

## Layout

```
src/maitre_d/        FastAPI app (Python 3.12, uv)
  restaurant.py      the restaurant's facts; the only definition of hours, tables, timezone
  timeslots.py       local wall-clock time to UTC, DST-safe validation, slot grid
  bookings.py        availability, alternatives, create/find/change/cancel
  speech.py          sentences the agent says aloud
  security.py        tool bearer auth, post-call webhook HMAC
  routes/            tools.py (ElevenLabs calls these), webhooks.py, dashboard.py, session.py
migrations/          Alembic
tests/               pytest, against real Postgres
agent/               ElevenLabs CLI project: agent, tool and test configs as JSON
knowledge/           knowledge-base documents (restaurant.md is generated)
scripts/             upload_kb.py, set_backend_host.py, bench_tools.py, seed.py
frontend/            Vite + React + TypeScript + Tailwind + TanStack Query
docs/                plan.md, decisions.md
```

## Commands

```bash
uv sync                                   # install backend + dev dependencies
uv run alembic upgrade head               # apply migrations
uv run python scripts/seed.py             # tables and some fictional bookings
uv run uvicorn maitre_d.main:app --reload --port 8000

uv run pytest                             # needs Postgres; see DATABASE_URL in .env.example
uv run ruff check . && uv run ruff format --check . && uv run mypy

cd frontend && npm run dev                # :5173, proxies /api to :8000
cd frontend && npm run generate:types     # after changing any backend response model
cd frontend && npm run lint && npm test && npm run build

# Expose the local backend to ElevenLabs (cloudflared is not on PATH on the dev machine)
"$LOCALAPPDATA/Programs/cloudflared/cloudflared.exe" tunnel --url http://localhost:8000
uv run python scripts/set_backend_host.py <hostname>.trycloudflare.com

# Agent config
elevenlabs agents push --dry-run         # always look before pushing
elevenlabs agents push
elevenlabs agents test <agent_id>         # costs credits; never in a loop
```

## Invariants

**The database prevents double-booking.** The `bookings_no_overlap` exclusion constraint is the
guarantee; the availability check in Python exists only to produce a good answer. Never remove or
weaken the constraint to make a test pass. A `23P01` error means the system worked.

**Every booking insert carries an idempotency key.** It is derived from the conversation ID and the
booking's identity (`docs/decisions.md` #2). An insert without a key is a duplicate waiting to
happen when the platform retries.

**Times are UTC in the database and London wall-clock time at the edges.** Convert with
`zoneinfo.ZoneInfo("Europe/London")`. Never add a fixed offset or build local slots by adding
`timedelta`s to UTC instants: that is off by an hour on half of the year's dates. "Today" means
today in London. `tzdata` is a dependency because Windows has no system timezone database.

**Tool endpoints return 200 for business outcomes.** Use `{ok, error, say, ...}`, where `say` is one
short sentence the agent can speak, with alternatives where they exist. Never put a stack trace,
an ID the caller didn't give, or someone else's data in `say`. Non-2xx means auth failure or a bug.

**A booking is visible only to the caller whose name and phone both match.** A wrong name with the
right phone returns exactly the same `not_found` as no booking. The reference alone authorises
nothing.

**Values the model must not choose come from the platform, not the prompt.** The conversation ID is
bound to `system__conversation_id` in the tool config. Don't add a tool parameter the LLM fills for
anything that authorises or deduplicates.

**Verify the webhook before parsing it.** HMAC over the raw body bytes, constant-time compare,
timestamp within ±30 minutes, all before `json.loads`.

**Anything a caller said is logged at DEBUG.** This covers names, phone numbers, messages and
transcripts. INFO and above carry the tool name, outcome and timing only.

**Restaurant facts have one definition.** Hours, tables and the timezone live in `restaurant.py`.
`knowledge/restaurant.md` is generated from it, and a test fails if the committed copy is stale.

**The agent config is code.** Change `agent/` files and push with the CLI. If something was changed
in the ElevenLabs dashboard, `elevenlabs agents pull` before editing, or the next push overwrites
it.

## Conventions

- **Python.** Type hints everywhere; mypy clean; ruff for lint and format. Pydantic v2 models for
  every request and response, so the OpenAPI document (and the generated frontend types) are the
  single source of the API's shape. Prefer plain functions and modules over classes and layers the
  code doesn't need.
- **Comments and docstrings explain why.** Say why the code is this way, especially where the
  obvious alternative is wrong. Don't narrate what the code does.
- **Tests are named as sentences** (`test_a_wrong_name_looks_exactly_like_no_booking`). The
  docstring states the behaviour, or the bug the test pins down. Booking tests run against real
  Postgres, because the guarantees under test live in the database. Before trusting a new test,
  break the code it covers and watch it fail.
- **Frontend.** TypeScript strict; API types come from `src/lib/api-schema.ts` (generated, never
  hand-edited); server state through TanStack Query.
- **Numbers.** Only measured numbers go in docs, comments, commit messages or the README, with the
  conditions they were measured under. If it wasn't measured, say so.
- **Decisions.** Record anything non-obvious in `docs/decisions.md`: the decision, the
  alternatives, and why. A few lines each.
- **Commits.** Small, one concern each. The subject says what changed for someone using the
  system. The body explains why, what was checked, and anything still wrong.

## Data and secrets

- All data is fictional. That covers the restaurant, the menu, and the guests. Phone numbers use
  the 07700 900000–900999 range Ofcom reserves for drama.
- Secrets live in `.env` (git-ignored): `ELEVENLABS_API_KEY`, `TOOL_BEARER_SECRET`,
  `ELEVENLABS_WEBHOOK_SECRET`, `DATABASE_URL`. `.env.example` lists them with no values. Never
  print, log or commit a secret value.

## Environment

The development machine runs Windows 11 with PowerShell 5.1 and Git Bash. CI runs Ubuntu.

- Write files as UTF-8 without a BOM.
- `.gitattributes` normalises line endings to LF.
- Use `pathlib` for paths; don't build them with string concatenation.
