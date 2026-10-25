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
`{ok: false, error, say, alternatives?}`. Request-schema failures also return 200, with a message
telling the LLM the expected format. Only auth failures (401) and bugs (500) are non-2xx.

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
