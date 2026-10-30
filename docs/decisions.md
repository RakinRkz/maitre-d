# Decisions

Each entry gives the decision, the alternatives, and why. Newest at the bottom. If a decision is
reversed, the old entry stays and a new one says what changed and what showed it was wrong.

---

## 1. The database enforces "no double-booking", not Python

**Decision.** A partial exclusion constraint on `bookings`:
`EXCLUDE USING gist (table_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE (status = 'confirmed')`,
using `btree_gist` so a plain integer can sit in a GiST index. On `23P01` the service tries the
next table that fits, then reports the slot as taken, with alternatives.

**Alternatives.**
- `SELECT … FOR UPDATE` on the table row before inserting.
- `SERIALIZABLE` isolation with retries.
- An advisory lock per table per evening.
- A check in Python alone.

**Why.** A check in Python alone is a race: two requests both see the table free and both insert.
Row locks and advisory locks work, but only if every code path remembers to take them. A new
endpoint or a manual SQL fix bypasses them. The constraint holds for every writer, including ones
that don't exist yet, and the default `READ COMMITTED` isolation is enough. Half-open `[)` ranges
let back-to-back sittings share a table. The `WHERE` clause means a cancelled booking frees its
table without being deleted.

## 2. Idempotency key = conversation ID + the booking's identity

**Decision.** `sha256(conversation_id, date, time, party_size, normalised name, E.164 phone)`,
stored in a `UNIQUE` column. The service looks the key up before inserting; a concurrent duplicate
loses on the unique constraint and returns the winner's booking. `notes` is excluded. Separately,
one conversation can't create two bookings for the same phone on the same date.

The key is kept when a booking is moved, so a late retry of the original create can't book the old
slot twice. That makes a key hit ambiguous: it is a true retry only when the stored booking is
confirmed and still matches the requested slot and party size; then it returns the booking as
`ok: true`. If the booking no longer matches — because `change_booking` moved it earlier in the
call — the reply is `ok: false`, `error: "already_booked_this_call"`, carrying the current booking.
The old slot is never reported as booked, and no duplicate is created. Cancelling clears the key,
so a cancelled row does not reach that branch.

**Alternatives.**
- Key on the conversation ID alone, which allows one booking per call.
- A client-generated key, but the LLM is the client and can't be trusted to keep one.
- No key, relying on the prompt.

**Why.** Duplicates come from two places: the model calling the tool twice, and a timed-out request
being retried. Both resend the same booking in the same conversation, which this key captures. The
conversation ID comes from the platform (`system__conversation_id`, bound as a dynamic variable),
so the model can't vary it. Keying on the conversation alone would block a legitimate "Friday and
Saturday, please". `notes` is free text that an LLM may reword on retry. The same-date rule catches
the case the key can't: the model treating "actually, 7:30" as a second booking instead of a
change.

## 3. The LLM supplies local date, weekday and time; the backend does the timezone work

**Decision.** Tools take `date` (`YYYY-MM-DD`), `weekday`, and `time` (`HH:MM`) in London wall-clock
time. The backend cross-checks the weekday against the date, localises with `zoneinfo`, stores UTC,
and builds slot grids in local time.

**Alternatives.**
- Ask the LLM for a full ISO 8601 timestamp with offset.
- A backend `resolve_datetime("next Friday around eight")` tool using a parsing library.

**Why.**
- **Against offsets from the LLM:** it would have to know whether a date falls in BST or GMT, and
  it gets that wrong around the changeover.
- **Against a parser tool:** it adds a round trip to every booking. "Around eight" still needs
  restaurant context (20:00, not 08:00) that the LLM already has.
- **For the weekday cross-check:** it is cheap and catches the common failure, where the caller
  said "Friday" and the model computed the wrong date.
- **Splitting the work:** the LLM does the language and Python does the calendar, where it can be
  unit-tested.

## 4. Tool outcomes are HTTP 200 with `ok` and a speakable `say`

**Decision.** Full slot, not found and invalid date all return 200 with
`{ok: false, error, say, alternatives?}`. Request-schema failures also return 200: `say` is a
generic caller-facing sentence ("Sorry, I didn't catch that. Could you say it again?") and the
field-by-field hints for the model travel in `detail`. Only auth failures (401) and bugs (500) are
non-2xx.

**Alternatives.** REST-style 404/409/422 bodies.

**Why.** The platform post-processes tool errors (`tool_error_handling_mode`: summarised,
passthrough, hidden), and the docs don't say what the model sees for each status code. A 200 puts
our exact sentence and alternatives in front of the model every time. Keeping 5xx for real
failures means a broken backend still looks broken.

## 5. Server tools authenticate with a bearer secret

**Decision.** `Authorization: Bearer <secret>`, held as an ElevenLabs workspace secret and compared
with `hmac.compare_digest`.

**Alternatives.** Request signing; IP allowlisting of ElevenLabs' egress addresses; OAuth2 client
credentials via an auth connection.

**Why.** The platform has no request signing for tool webhooks (only post-call webhooks are
signed). An IP allowlist breaks silently when the published ranges change. OAuth2 needs a token
endpoint this project doesn't otherwise need. A long random secret over TLS, never shown to the
model, is the simplest thing that works. A constant-time compare means response timing reveals
nothing about how much of a guess was right.

## 6. Our own HMAC verifier instead of the SDK helper

**Decision.** About 20 lines in `security.py`:
- parse `t=…,v0=…`
- compute HMAC-SHA256 over `f"{t}.".encode() + raw_body`
- compare in constant time
- reject timestamps more than 30 min old **or** more than 30 min in the future

**Alternatives.** `ElevenLabs(...).webhooks.construct_event(...)` from the `elevenlabs` SDK.

**Why.** The SDK helper (2.74.1) compares with `!=`, which is not constant time. It accepts any
future timestamp, and it needs an API client instance just to verify. Pulling in a large generated
SDK for one function, and then working around it, is worse than code we can read and test
directly.

## 7. Menu in the knowledge base as `prompt`, not RAG

**Decision.** `menu.md` and `restaurant.md` are attached with `usage_mode: "prompt"`, so they are
always in context.

**Alternatives.** RAG (`usage_mode: "auto"` with RAG enabled).

**Why.** The menu is a few kilobytes. RAG adds about 250 ms a turn (per the docs) and can miss a
chunk. A missed chunk on an allergen question is a safety problem, not just a wrong answer. RAG
becomes the right call when the content outgrows the prompt (a wine list, several menus, supplier
spec sheets), and the switch is a one-line config change.

## 8. Evals are ElevenLabs agent tests, run from the CLI

**Decision.** Test configs live in `agent/test_configs/` and run with `elevenlabs agents test`,
which exits non-zero on failure. Simulations mock tool responses when a scenario needs a specific
state.

**Alternatives.**
- The `simulate-conversation` endpoint with our own runner and judge.
- A hand-rolled LLM-vs-LLM harness.

**Why.** `simulate-conversation` is deprecated and removed on 31 Oct 2026. Agent tests are the
platform's supported replacement. They include tool-call assertions and per-test tool mocks, and
their configs round-trip through the CLI as files. Mocks keep scenarios independent of whatever
happens to be in the dev database.

## 9. WebRTC conversation tokens for the browser

**Decision.** `/api/session` calls `GET /v1/convai/conversation/token` server-side and returns only
the token. The agent has `enable_auth: true`.

**Alternatives.** A signed WebSocket URL; a public agent ID.

**Why.** A public agent ID lets anyone spend the account's minutes. Both signed URLs and tokens
keep the API key on the server. The SDK uses WebRTC for voice by default, and WebRTC brings echo
cancellation and jitter handling that a raw WebSocket audio stream doesn't.

## 10. asyncpg as the Postgres driver

**Decision.** SQLAlchemy 2.0 async with `asyncpg`; Alembic with the async template.

**Alternatives.** psycopg 3, which has one driver for sync and async and good error diagnostics.

**Why.** psycopg 3's async mode doesn't run on Windows' default `ProactorEventLoop`. Development
happens on Windows, so every entry point and the test runner would need an event-loop policy
override. asyncpg works with the default loop, and its errors still carry `sqlstate` and
`constraint_name`, which is all the service needs to tell "table taken" from "duplicate request".

## 11. httpx, not the `elevenlabs` SDK, for runtime calls

**Decision.** The backend makes its one runtime call (conversation token) with `httpx`. The scripts
(knowledge-base upload, environment variable update) use `httpx` as well.

**Alternatives.** The official Python SDK.

**Why.** It is one GET with an `xi-api-key` header. The SDK is a large generated package, so httpx
keeps the dependency list short. It also lets tests swap in `httpx.MockTransport` without patching
SDK internals. If the scripts grow to use many endpoints, the SDK becomes worth it.

## 12. Tool URLs come from ElevenLabs environment variables

**Decision.** The committed tool configs use `https://{{system__env_backend_host}}/tools/...`. The
`development` environment points at the local tunnel and `production` at the deployment.
`scripts/set_backend_host.py` updates the development value when the tunnel restarts.

**Alternatives.**
- Rewrite the URLs in the tool JSON and push on every tunnel restart.
- A stable named tunnel, which needs a domain on Cloudflare.
- ngrok's free static domain.

**Why.** The agent config stays a reviewable file that doesn't churn with every dev session, and
one agent serves both environments. ngrok is the fallback if environment variables prove awkward
in M2.

## 13. One deployable: FastAPI serves the built dashboard

**Decision.** `vite build` output is served by FastAPI in production; Vite's dev server proxies
`/api` in development.

**Alternatives.** The frontend on a static host and the API elsewhere.

**Why.** One URL, no CORS, one thing to deploy and keep warm. The dashboard is small and doesn't
need a CDN.

## 14. Frontend API types are generated from OpenAPI, with a drift gate

**Decision.** `openapi-typescript` generates the frontend's types from the backend's OpenAPI
document. CI regenerates them and fails if the committed copy differs.

**Alternatives.** Hand-written TypeScript interfaces.

**Why.** Pydantic models become the single definition of the API's shape. Hand-mirrored types
drift silently, and the first sign is a blank field in the UI.

## 15. Transcript as JSONB; tool invocations as rows

**Decision.** `calls.transcript` stores the platform's turns as received. `tool_invocations` is a
normalised table that our own server writes.

**Alternatives.** Normalise transcript turns into a `call_turns` table; or keep only the
platform's tool data.

**Why.** A transcript is only ever read whole, to draw one call, so normalising it buys nothing.
Tool invocations are queried across calls (p50/p95 per tool), and they record what our server
measured, which the platform's numbers can't: the platform sees round-trip time, while we see
handler time. The difference between them is the network and platform overhead.

---

*Decisions 16–20 came out of building and testing M1.*

## 16. Booking writes for a day queue on an advisory lock

**Decision.** Before inserting or moving a booking, the transaction takes
`pg_advisory_xact_lock(namespace, day)` for the booking's local day. It does **not** re-read
availability after getting the lock.

**What showed this was needed.** The first run of the race test forced twelve callers to read
availability before any of them wrote, and one request failed with `deadlock detected`, not with
an exclusion violation. Postgres checks an exclusion constraint after the new row is already in
the index, so two transactions inserting clashing rows at the same instant can each find the
other's row and wait for it. The Postgres source says as much (`execIndexing.c`: "There is a
chance of deadlock, if two backends insert a tuple at the same time"). The deadlock detector
resolves it, but only after `deadlock_timeout` (1 s by default). Retrying the same table after
a deadlock was correct but slow: in a twelve-way pile-up the cycles kept re-forming, and the test
took 47 s. With the per-day queue it took 0.37 s, with no deadlocks.

**Alternatives.**
- **Treat a deadlock as a lost race and retry.** Correct, but each deadlock costs a second of
  silence on the phone.
- **Lower `deadlock_timeout`.** It needs superuser rights, which managed Postgres doesn't give.
- **`SELECT … FOR UPDATE` on the table row.** A request that loses on one table keeps that row
  locked while it tries the next, and `change_booking` tries tables in a different order, which
  risks lock-order deadlocks.
- **Re-read availability once the lock is held,** and drop the constraint. That moves the
  guarantee back into Python, which #1 rejected.

**Why.** One lock per day can't form a lock-order cycle. It's held only for the few milliseconds
of an insert and commit, and a restaurant takes nowhere near enough bookings for a day to make
that queue matter. Because availability isn't re-read, which caller gets a table is still decided
by the constraint. The test that drops the constraint still double-books, so it still proves that.
The deadlock itself depends on timing inside the server and doesn't happen on every run, so a
separate test pins down the queue's behaviour directly.

## 17. Names match by words, ignoring case, accents, punctuation and titles

**Decision.** Every word the caller gives must appear in the booking's name. "Ahmed", "Nadia" and
"Mrs Ahmed" all match "Nadia Ahmed"; "Nadia Khan" doesn't. The phone number must match exactly
after E.164 normalisation.

**Alternatives.**
- Exact full-name match, which fails whenever a caller gives only a surname, or uses a title.
- Fuzzy matching (edit distance), which absorbs speech-recognition errors but also lets near
  guesses through.

**Why.** The phone number is the strong factor. The name stops someone who knows only a number
from hearing what's booked against it, so it should be strict about wrong names and relaxed about
how a right name is said. Fuzzy matching is the next step if real calls show misheard spellings
failing; it is a deliberate loosening, not a default.

## 18. Phone numbers must be "possible", not "valid"

**Decision.** `phonenumbers.is_possible_number_with_reason(...) == IS_POSSIBLE`, with UK as the
default region.

**Alternatives.** `is_valid_number`, which checks the number against allocated ranges.

**Why.** `is_valid_number` rejects the Ofcom drama range (07700 900xxx) that all the fictional
data uses, because those numbers are deliberately unallocated. More importantly, it would turn
away a real caller whose newly allocated number isn't in the library's metadata yet. The plain
`is_possible_number` was too loose: it accepted "0770" as a possible *local* number. A test caught
that.

## 19. Tool latency is timed by ASGI middleware and logged after the response

**Decision.** `ToolInvocationLog` wraps every `/tools/*` request. Its clock starts when the request
reaches the app and stops after the last byte of the response is sent. It then writes the
`tool_invocations` row.

**Alternatives.**
- Timing inside each handler, which misses parsing, validation, auth failures and serialisation.
- FastAPI `BackgroundTasks`.
- Logs only, with no database row.

**Why.** Middleware covers every request, including the ones refused before a handler runs. The
write comes after the final `send`, so it is not in the caller's pause, and that was measured,
not assumed. A second server with an artificial 50 ms sleep before the write moved the client
round trip from 5.9 to 6.9 ms (p50, 40 requests), not by 50 ms, because uvicorn completes the
response at the final `send`.

Measured on the development laptop (Windows 11, Postgres 17.11 on localhost, one uvicorn worker),
sequentially, 200 booking lifecycles after 10 warm-up rounds, 2026-10-25:

| tool | server p50 / p95 ms | client p50 / p95 ms |
|---|---|---|
| check_availability | 2.3 / 2.9 | 5.9 / 7.7 |
| create_booking | 5.3 / 7.1 | 9.0 / 11.6 |
| find_booking | 2.2 / 3.4 | 5.9 / 8.4 |
| change_booking | 5.7 / 7.4 | 9.5 / 12.2 |
| cancel_booking | 4.2 / 5.6 | 8.0 / 10.5 |

Most of the client-to-server gap is the HTTP stack on this machine. `/api/health`, with no
database and no logging, has a client round trip of 2.8 ms p50. These are local numbers; the
deployed ones (M5) will include the real network between ElevenLabs and the host.

## 20. Dining tables are reference data in code, synced to the database

**Decision.** `restaurant.TABLES` defines the tables. `sync_dining_tables()` upserts them into
`dining_tables`, and is run by `scripts/seed.py` and the test fixtures. Availability reads seat
counts from `restaurant.py`, not from the database.

**Alternatives.**
- Insert the rows in a migration.
- Make the database the only source and query it for every availability check.

**Why.** Tables, like opening hours, are facts about the restaurant, and keeping them in one
module keeps them in one place. A migration that copies them would be a second definition, frozen
on the day it was written. Reading seats from code saves a query on every check. The database
table still exists, so bookings can hold a foreign key and the dashboard can show labels.

## 21. Built for the Free plan: no public live agent

**Decision.** The project runs on the ElevenLabs Free plan (15 voice minutes a month, no overage)
and pays for no hosting. The deliverable is the repository, a README with measured numbers, and a
recorded call. Public deployment is a stretch goal, and even then without a public "Talk" button.

**Alternatives.**
- Starter ($6 a month, 75 minutes) with a deployed, password-gated demo.
- Free hosting tiers with a public "Talk" button.

**Why.** The budget is zero. On Free, a public button is a liability: one curious visitor can
spend the month's minutes, and the link then fails for the next person. Free hosts sleep when
idle, and a sleeping backend makes the first tool call of a call a long silence, which would show
off the latency work at its worst. Every hard problem in the project lives in the backend, so it
can be shown, tested and measured without a public deployment. Anyone who wants to talk to the
agent can run it with their own key; the README makes that a few commands.

## 22. A tool reply separates what the caller hears from what the model reads

**Decision.** `ToolReply` has two text fields. `say` is one short sentence the agent reads aloud,
written for the caller, with no field names or formats in it. `detail` is optional guidance for
the model — currently the field hints on a schema failure — and is never spoken. Successful
replies omit `detail` through `response_model_exclude_none=True`.

**Alternatives.**
- One `say` field carrying both the sentence and the hints, which is what M1 started with. The
  model read "The request was not valid: date must be YYYY-MM-DD…" and could say it to a caller.
- A generic `say` with no hints anywhere, leaving the model to guess from FastAPI's 422 body.
- A separate error field per field, which the model has to reassemble before it can retry.

**Why.** The two readers want opposite things. A caller needs a short, natural sentence and cannot
act on "date must be YYYY-MM-DD"; the model needs the exact field and format to fix its next call
and gains nothing from conversational padding. Splitting them lets each be written and tested for
its own reader, and keeps schema vocabulary out of the voice. It also leaves room to add model
guidance later without risking it being read aloud.
