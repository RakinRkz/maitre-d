# Maître D: working on this codebase

A voice agent that takes table bookings for Saffron Table, a fictional London restaurant. It runs
on the ElevenLabs Agents Platform. A FastAPI backend owns the booking domain and exposes it to the
agent as server tools (webhooks). Postgres stores bookings and calls. A React dashboard shows
bookings, calls and a per-call timeline, and can start a web voice call.

`docs/plan.md` is the design: architecture, data model, tool contract, milestones.
`docs/decisions.md` records why things are the way they are. Read both before changing anything
structural.

> **Status: M1 built, reviewed and fixed** (booking domain, server-tool endpoints, tests), committed
> in two parts. Not built yet: the agent config, `/api/session`, the post-call webhook,
> the dashboard, CI. Lines below marked *(later)* describe those. Update this file when a milestone
> changes what is true.

## Layout

```
src/maitre_d/
  main.py            create_app() factory: middleware, routers, validation-error handling
  config.py          settings from the environment and .env
  db.py              engine and session helpers; sync_dining_tables()
  models.py          SQLAlchemy tables, mirroring the migration; constraint-name constants
  restaurant.py      the restaurant's facts; the only definition of hours, tables, timezone
  timeslots.py       local wall-clock time to UTC, DST-safe validation, slot grid
  bookings.py        availability, alternatives, and each booking tool's logic
  speech.py          caller-facing sentences; model guidance lives in routes/tools.py
  schemas.py         tool request/response models: the contract with the agent
  security.py        tool bearer auth (post-call webhook HMAC comes in M3)
  invocations.py     ASGI middleware that times every /tools/* request and logs it to the DB
  routes/tools.py    the server tools, one POST per tool, path = tool name
migrations/          Alembic; 0001 is hand-written, `alembic check` confirms the models agree
tests/               pytest against real Postgres; helpers.py holds shared builders,
                     test_config.py for settings/auth, test_db.py for dining-table sync
scripts/             create_databases.py, seed.py, bench_tools.py
docs/                plan.md, decisions.md
agent/, knowledge/, frontend/   (later)
```

## Commands

```bash
uv sync                                   # install backend + dev dependencies
uv run alembic upgrade head               # apply migrations to DATABASE_URL
uv run alembic check                      # fails if models.py and the migrations disagree
uv run python scripts/seed.py --demo      # tables plus a week of fictional bookings
uv run uvicorn maitre_d.main:create_app --factory --reload --port 8000

uv run python scripts/create_databases.py # create both databases; safe to re-run
uv run pytest                             # rebuilds TEST_DATABASE_URL from the migrations
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run python scripts/bench_tools.py      # p50/p95 per tool against a running server

# Local Postgres on the Windows dev machine: portable binaries, data in .pgdata/ (git-ignored)
PG="$LOCALAPPDATA/Programs/postgresql-17/bin"
"$PG/pg_ctl.exe" -D .pgdata -l .pgdata/server.log start      # and `stop`
# Anywhere with Docker instead: docker compose up -d, then create_databases.py on a fresh volume

# (later)
cd frontend && npm run dev                # :5173, proxies /api to :8000
cd frontend && npm run generate:types     # after changing any backend response model
cd frontend && npm run lint && npm test && npm run build

# Expose the local backend to ElevenLabs (cloudflared is not on PATH on the dev machine)
"$LOCALAPPDATA/Programs/cloudflared/cloudflared.exe" tunnel --url http://localhost:8000
uv run python scripts/set_backend_host.py <hostname>.trycloudflare.com

# Agent config (the CLI runs through npx; there is no global install)
npx @elevenlabs/cli agents push --dry-run    # always look before pushing
npx @elevenlabs/cli agents push
npx @elevenlabs/cli agents test <agent_id>   # costs credits; never in a loop
```

**The ElevenLabs account is on the Free plan:** 15 voice minutes a month and no overage. Never
start a voice call or an agent-test run without first saying what it will spend. Pushing configs
and creating tools, agents and knowledge-base documents cost nothing. The budget is in
`docs/plan.md` section 14.

## Invariants

**The database prevents double-booking.** The `bookings_no_overlap` exclusion constraint is the
guarantee; the availability check in Python exists only to produce a good answer. Never remove or
weaken the constraint to make a test pass. A `23P01` error means the system worked.

**Booking writes for a day queue behind each other, and that is all the queue does.**
`_queue_for_day` takes a per-day advisory lock so that simultaneous clashing inserts don't
deadlock inside Postgres's exclusion check (decisions #16). It must not re-read availability
after taking the lock; if it did, the race test without the constraint would stop proving that the
constraint is what decides.

**Every booking insert carries an idempotency key.** It is derived from the conversation ID and the
booking's identity (`docs/decisions.md` #2). An insert without a key is a duplicate waiting to
happen when the platform retries.

**Times are UTC in the database and London wall-clock time at the edges.** Convert with
`zoneinfo.ZoneInfo("Europe/London")`. A fixed offset is wrong for half the year; stepping UTC
instants to build a grid, or taking UTC midnight as the start of a day, is wrong on clock-change
days. Build in wall-clock time, then localise. "Today" means today in London. `tzdata` is a
dependency because Windows has no system timezone database.

**Tool endpoints return 200 for business outcomes.** Use `{ok, error, say, detail, ...}`, where
`say` is one short sentence the agent can speak, with alternatives where they exist, and `detail`
is optional guidance for the model that is never spoken. Never put a stack trace, an ID the caller
didn't give, or someone else's data in `say`. Non-2xx means auth failure or a bug.

**A booking is visible only to the caller whose name and phone both match.** A wrong name with the
right phone returns exactly the same `not_found` as no booking. The reference alone authorises
nothing.

**Values the model must not choose come from the platform, not the prompt.** The conversation ID is
bound to `system__conversation_id` in the tool config. Don't add a tool parameter the LLM fills for
anything that authorises or deduplicates.

**Verify the webhook before parsing it** *(M3)*. HMAC over the raw body bytes, constant-time
compare, timestamp within ±30 minutes, all before `json.loads`.

**Anything a caller said is logged at DEBUG.** This covers names, phone numbers, messages and
transcripts. INFO and above carry the tool name, outcome and timing only.

**Restaurant facts have one definition.** Hours, tables and the timezone live in `restaurant.py`.
`dining_tables` is synced from it by `sync_dining_tables()`; `knowledge/restaurant.md` will be
generated from it *(M2)*, with a test that fails if the committed copy is stale.

**Tool timing is measured by middleware, and the log write happens after the response.** Don't
move the `tool_invocations` insert before the final `send`: it would add a database round trip
to every pause the caller hears.

**The agent config is code** *(M2)*. Change `agent/` files and push with the CLI. If something was changed
in the ElevenLabs dashboard, `npx @elevenlabs/cli agents pull` before editing, or the next push overwrites
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
  Postgres, because the guarantees under test live in the database. Race tests use the
  `reads_before_writes` fixture to force every request to read before any writes; without it, a
  race test can pass without a race. Before trusting a new test, break the code it covers and
  watch it fail.
- **Clock.** Domain functions take `now` as an argument; routes get it from the `get_now`
  dependency, which tests override. Nothing calls `datetime.now()` inside the domain.
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
