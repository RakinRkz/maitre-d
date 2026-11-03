# Maître D: working on this codebase

A voice agent that takes table bookings for Saffron Table, a fictional London restaurant. It runs
on the ElevenLabs Agents Platform. A FastAPI backend owns the booking domain and exposes it to the
agent as server tools (webhooks). Postgres stores bookings and calls. A React dashboard shows
bookings, calls and a per-call timeline, and can start a web voice call.

`docs/plan.md` is the design: architecture, data model, tool contract, milestones.
`docs/decisions.md` records why things are the way they are. Read both before changing anything
structural.

> **Status: built, tested, committed and pushed; exercised by three live calls.** Everything in
> the plan except the public deployment exists: the booking domain and server tools, the post-call
> webhook, `/api/session`, the dashboard (read API and React frontend), the agent as code, the
> knowledge base, CI, and agent evals. The platform side is set up as of 2026-11-02: the secret, the
> six tools, the five tests, the agent and the post-call webhook exist on the ElevenLabs account,
> and `backend_host` points at a quick tunnel (the owner starts it; the environment refuses to let
> the assistant, see `docs/wip.md`). Three real calls have been made (2026-11-03); the first booked a
> table end to end. Tool latency is a few samples, and the agent tests have never been run. Update this file
> when that changes.

## Layout

```
src/maitre_d/
  main.py            create_app() factory: middleware, routers, static dashboard, error handling
  config.py          settings from the environment and .env
  db.py              engine and session helpers; sync_dining_tables()
  models.py          SQLAlchemy tables, mirroring the migrations; constraint-name constants
  restaurant.py      the restaurant's facts; the only definition of hours, tables, timezone
  timeslots.py       local wall-clock time to UTC, DST-safe validation, slot grid
  bookings.py        availability, alternatives, and each booking tool's logic
  speech.py          caller-facing sentences; model guidance lives in routes/tools.py
  schemas.py         tool request/response models: the contract with the agent
  dashboard_schemas.py  response models for the dashboard's read API
  security.py        tool bearer, webhook HMAC verification, and the local-only guard
  invocations.py     ASGI middleware: times /tools/* requests, caps bodies, logs answered ones
  elevenlabs.py      every outbound call to the ElevenLabs REST API (httpx, no SDK)
  envfile.py         sets one key in .env without touching the rest
  knowledge.py       renders knowledge/restaurant.md from restaurant.py
  routes/tools.py    the server tools, one POST per tool, path = tool name
  routes/webhooks.py the post-call webhook: raw body, verify, then parse and upsert `calls`
  routes/session.py  POST /api/session: a conversation token for the browser, never the key
  routes/dashboard.py  GET /api/bookings, /calls, /messages, /metrics/tool-latency
migrations/          Alembic; hand-written, `alembic check` confirms the models agree
tests/               pytest, mostly against real Postgres; helpers.py holds shared builders
scripts/             setup_elevenlabs.py, create_webhook.py, set_backend_host.py, run_agent_tests.py
                     (the ElevenLabs side); seed.py, create_databases.py, bench_tools.py (local);
                     generate_knowledge.py, sync_agent_prompt.py, export_openapi.py (generated files)
agent/               the ElevenLabs agent as code: prompts/ (Markdown source of the prompt),
                     agent_configs/, tool_configs/ (one JSON per server tool), test_configs/
knowledge/           menu.md (hand-written) and restaurant.md (generated, never edited)
frontend/            Vite + React + TypeScript dashboard; openapi.json and src/lib/api-schema.ts
                     are generated from the backend's models
docs/                plan.md (design), decisions.md (why), wip.md (state and open items)
.github/workflows/   ci.yml
```

## Commands

```bash
uv sync                                   # install backend + dev dependencies
uv run alembic upgrade head               # apply migrations to DATABASE_URL
uv run alembic check                      # fails if models.py and the migrations disagree
uv run python scripts/seed.py --demo      # tables, a week of bookings, two fictional calls
uv run uvicorn maitre_d.main:create_app --factory --reload --port 8000

uv run python scripts/create_databases.py # create both databases; safe to re-run
uv run pytest                             # rebuilds TEST_DATABASE_URL from the migrations
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run python scripts/bench_tools.py      # p50/p95 per tool against a running server

# Generated files: each has a --check that CI runs, and a test or gate that fails when stale
uv run python scripts/generate_knowledge.py     # knowledge/restaurant.md, from restaurant.py
uv run python scripts/sync_agent_prompt.py      # agent/prompts/*.md into the agent config
uv run python scripts/export_openapi.py         # frontend/openapi.json, from the Pydantic models

cd frontend && npm run dev                # :5173, proxies /api to :8000
cd frontend && npm run generate:types     # after export_openapi.py: src/lib/api-schema.ts
cd frontend && npm run typecheck && npm run lint && npm test && npm run build

# Local Postgres on the Windows dev machine: portable binaries, data in .pgdata/ (git-ignored)
PG="$LOCALAPPDATA/Programs/postgresql-17/bin"
"$PG/pg_ctl.exe" -D .pgdata -l .pgdata/server.log start      # and `stop`
# Anywhere with Docker instead: docker compose up -d, then create_databases.py on a fresh volume

# The ElevenLabs workspace. Safe to re-run: every object is found by name and updated.
uv run python scripts/setup_elevenlabs.py --dry-run   # the plan; no network
uv run python scripts/setup_elevenlabs.py             # secret, variables, docs, tools, tests, agent
# Expose the local backend (cloudflared is not on PATH on the dev machine), then point the
# platform at it. A quick tunnel gets a new hostname every start, so repeat both each time.
"$LOCALAPPDATA/Programs/cloudflared/cloudflared.exe" tunnel --url http://localhost:8000
uv run python scripts/set_backend_host.py <hostname>.trycloudflare.com
uv run python scripts/create_webhook.py <hostname>.trycloudflare.com   # then restart the backend
uv run python scripts/run_agent_tests.py                # lists the tests; runs nothing
uv run python scripts/run_agent_tests.py book_next_friday --confirm-spend   # costs credits
```

**The ElevenLabs account is on the Free plan:** 15 voice minutes a month and no overage. Never
start a voice call or an agent-test run without first saying what it will spend. Creating tools,
agents and knowledge-base documents costs nothing. The budget is in `docs/plan.md` section 14.
`run_agent_tests.py` refuses to run without `--confirm-spend`; the cost of a test run is not
documented by the platform, so run one test, read the `credits_used` it reports, and decide then.

**The workspace is provisioned by `scripts/setup_elevenlabs.py`, not by hand-run CLI commands**
(decisions #27). The CLI is for reading and for `agents pull`. `--dry-run` is not a promise there:
on 2026-10-30 `agents add --dry-run` still tried to create the agent, so when trying a CLI command
for the first time add `--base-url http://127.0.0.1:9` so a request that should not be sent has
nowhere to go. Reading is safe (`agents list`, `webhooks list`, `environment-variables list`).
Anything that creates, updates or deletes changes the user's account even when it costs nothing,
and starting a tunnel or writing a secret to the workspace needs their explicit OK: the
environment itself refused both during M2 until they are allowed.

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

**Verify the webhook before parsing it.** HMAC over the raw body bytes, constant-time compare,
timestamp within ±30 minutes on both sides, all before the body is parsed. The body is read with a
size cap first, because it has to be read before anyone is authenticated (decisions #23).

**Routes with no login are local-only.** The tunnel that lets ElevenLabs reach the tools publishes
the whole app, so `/api/session` and the dashboard routes refuse a request that came through a
proxy, from another host, with a `Host` other than localhost (DNS rebinding), or labelled
cross-site by the browser (`require_local_request`, decisions #24). A new route that shows
guests' details or spends voice minutes gets this dependency. The tools and the webhook must not:
they are protected by their own secrets, and ElevenLabs reaches them through the tunnel.

**Anything a caller said is logged at DEBUG.** This covers names, phone numbers, messages and
transcripts. INFO and above carry the tool name, outcome and timing only.

**Restaurant facts have one definition.** Hours, tables and the timezone live in `restaurant.py`.
`dining_tables` is synced from it by `sync_dining_tables()`; `knowledge/restaurant.md` is
generated from it by `scripts/generate_knowledge.py`, and a test fails if the committed copy is
stale. The agent's prompt must not repeat hours or the address; a test checks that too.

**Tool timing is measured by middleware, and the log write happens after the response.** Don't
move the `tool_invocations` insert before the final `send`: it would add a database round trip
to every pause the caller hears. Only requests the app answered (200) or failed on (5xx) are
stored; refusals (401, 404, 405, 413) are a log line only, because anyone who finds the tunnel
hostname can produce them. Bodies over 64 KB are refused before the app reads them. The engine is
created with `hide_parameters=True` so a failed write can't put a guest's details in an error log
(decisions #19, reproduced before it was fixed).

**Nothing the platform sends us is stored without thought for what it contains.** The webhook
removes each tool call's request headers before storing a transcript, because they can carry the
bearer token. Treat any new field from the platform the same way before it reaches `calls`.

**The agent config is code.** Change `agent/` files and push with the CLI. If something was changed
in the ElevenLabs dashboard, `npx @elevenlabs/cli agents pull` before editing, or the next push
overwrites it. The prompt is edited as Markdown in `agent/prompts/` and embedded in the config by
`scripts/sync_agent_prompt.py`; never edit the `prompt` string in the JSON, and a test fails if
the two differ. `tests/test_agent_config.py` also keeps each tool config in step with its request
model, so change both together.

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

- **WIP doc.** Update `docs/wip.md` as part of the change, not as an afterthought. After a
  significant code change (a new tool, a changed reply contract, a fix that lands a checklist
  item), tick the affected findings and coverage items, move unshipped M2/M5 items to the M2
  checklist, record measured results and conditions, and update the status line before the
  milestone it belongs to closes. If a wave finishes without updating it, the wave is not done.

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
