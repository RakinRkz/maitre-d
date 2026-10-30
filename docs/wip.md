# WIP — Maître D

Working notes for the milestone in progress. The design is in `docs/plan.md`; the reasons are in
`docs/decisions.md`. This file is for state, open items and next actions, and can be folded into
those or deleted once M1 is committed and M2 starts.

Last updated: 2026-10-30.

## Status

- **M1 (booking domain + server tools): built, reviewed and fixed; committed in two parts.** The
  only earlier commit is M0 ("Plan the booking agent before writing any of it").
- Everything under `src/`, `tests/`, `migrations/`, `scripts/`, plus `pyproject.toml`, `uv.lock`,
  `alembic.ini`, `docker-compose.yml`, `.env.example`, `.python-version` and this file is tracked.
  `CLAUDE.md`, `docs/plan.md` and `docs/decisions.md` were updated by the M1 commits.
- **Verified on 2026-10-30** (Windows 11, local Postgres 17.11, pytest through httpx's ASGI
  transport; the test schema is rebuilt from the migrations):
  - `uv run pytest` → **104 passed in 6.34s** (includes the new `tests/test_db.py`).
  - `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` → clean.
  - `uv run alembic check` → "No new upgrade operations detected".
  - `scripts/create_databases.py` reports "already exists" on both real databases, and created then
    reported "already exists" for a throwaway probe database (since dropped).
- Local Postgres is running for this verification. Start or stop it with the command below.
- A fresh setup creates `maitre_d` and `maitre_d_test` with `scripts/create_databases.py`; the test
  database is no longer a manual prerequisite.

## What exists

| Area | Files |
|---|---|
| Restaurant facts | `src/maitre_d/restaurant.py` — timezone, services, tables, limits |
| Time handling | `src/maitre_d/timeslots.py` — wall-clock to UTC, DST, validation, slot grid |
| Booking domain | `src/maitre_d/bookings.py` — availability, alternatives, six tool behaviours |
| Speech | `src/maitre_d/speech.py` — every caller-facing sentence |
| Tool contract | `src/maitre_d/schemas.py` — request/response Pydantic models |
| HTTP | `src/maitre_d/routes/tools.py`, `src/maitre_d/main.py` |
| Auth | `src/maitre_d/security.py` — tool bearer |
| Latency log | `src/maitre_d/invocations.py` — ASGI middleware, writes after the response |
| Storage | `migrations/versions/0001_booking_domain.py`, `src/maitre_d/models.py` |
| Tests | `tests/` — 104 tests, real Postgres, race + DST + access + HTTP |
| Tooling | `scripts/create_databases.py`, `scripts/seed.py`, `scripts/bench_tools.py` |

Not built yet: the agent config (`agent/`), knowledge base (`knowledge/`), `/api/session`, the
post-call HMAC webhook, the dashboard (`frontend/`), CI, README.

## Review findings

Verdict: **no defect found in the guarantees under test** — the exclusion constraint, idempotency
key, per-day queue, DST handling and name+phone access control hold up. The items below were setup,
contract and wording issues; all seven and the coverage list are now fixed and verified.

### Before committing M1

- [x] **1. Alembic and seed require `TOOL_BEARER_SECRET`.** `config.py:17` makes it mandatory;
  `migrations/env.py:9,24-25` calls `get_settings()` only for `DATABASE_URL`, and `scripts/seed.py:42`
  does the same. Confirmed: `Settings()` with `DATABASE_URL` set but no secret raises
  `tool_bearer_secret Field required`. This breaks `alembic upgrade head` in CI and seed on a fresh
  machine. **Also a security bug:** an empty secret currently authenticates an empty bearer token —
  `"Bearer ".partition(" ")` yields an empty token, and `hmac.compare_digest(b"", b"")` is true —
  and `.env.example` ships `TOOL_BEARER_SECRET=` empty. Treat `None` **and empty** as unconfigured,
  return 500, and add a test. Fix the config coupling by making the secret optional in `Settings`
  and failing closed in `security.require_tool_bearer`, or by reading `DATABASE_URL` directly in
  `env.py`.
- [x] **2. A fresh checkout cannot run the tests.** `docker-compose.yml:8` creates only
  `maitre_d`; `tests/conftest.py:45-50` recreates the schema, not the database, so
  `maitre_d_test` is missing. Also `tests/conftest.py:40` reads `os.environ` only, while
  `pydantic-settings` reads `.env` into `Settings` without exporting it — a `TEST_DATABASE_URL` in
  `.env` is silently ignored. Fix: add `test_database_url` to `Settings`, use it in `conftest`, and
  add a small create-databases script (or compose init script); document it.
- [x] **3. `weekday` without `date` is silently ignored.** `schemas.py:109-122` enforces only
  `date` → `weekday`; `bookings.py:484` drops a lone `weekday`. Observed: `change_booking` with only
  `weekday: "Saturday"` returns `ok: true`, "Nothing needed changing". Fix: reject `weekday` without
  `date`, and add a test.
- [x] **4. Caller-facing strings use the third person.** `say` is read aloud, so
  `speech.py:106-110` and `:138` ("Did the caller mean…"), `:190-194` ("This call already
  booked…"), `:210-216` ("for a table for…") and `:172-173` ("Could you ask for it again?") need
  rewording. Update the exact-string tests in `test_timeslots.py:79,92` and
  `test_idempotency.py:83-86`.
- [x] **5. `say` mixes caller speech with model guidance.** `routes/tools.py:75-100` +
  `speech.py:231-232` make an invalid request say "The request was not valid: date must be
  YYYY-MM-DD…". Decide before writing the M2 tool configs: keep `say` caller-facing and add a
  `detail` field for the model, or make `say` generic and put the hints in `detail`.
- [x] **6. Idempotency key is stale after a date change.** `create_booking` returns any booking
  matching the key (`bookings.py:389-393`); `change_booking` keeps the original key (`:502-511`).
  Observed: book Friday → change to Saturday → create Friday again returns `ok: true` with the
  Saturday booking. No duplicate is created, but `ok: true` is misleading and a legitimate second
  Friday booking is blocked. Decide the semantics, record it in `decisions.md` #2, and pin it with
  a test.
- [x] **7. Test hygiene.** `test_booking_tools.py:208` (`assert again == again | {...}`) reads like
  a tautology; use explicit asserts, including `booking["status"] == "cancelled"`.

### Coverage to add with the fixes

- [x] `change_booking` to a new date (only time changes are covered end to end), and a
  party-size-only change.
- [x] `weekday` without `date` is rejected.
- [x] The stale-key case above.
- [x] `find_booking` with two bookings (plural `speech.found` is untested).
- [x] `take_message` with an unparseable phone keeps the raw number.
- [x] `sync_dining_tables` is idempotent.

### After M1 / during M2–M5

- **Invalid phone in change/cancel** returns `not_found` (via `_verified_booking`) while
  find/create return `invalid_phone`. Validate the phone first; no security loss.
- **`FIELD_HINTS` mislabels length errors**: a too-long `name` says "name is required"; a 101-guest
  party says "must be a whole number". Inspect `error["type"]` or make the hints range-aware.
- **`sync_dining_tables` overclaims** (`db.py:37-50`): it upserts but never removes tables, so it
  does not make `dining_tables` match `restaurant.TABLES` if one is removed. Delete stale rows
  (mind the FK) or reword.
- **0 ms 500s**: `invocations.py:47,62-63,68` leaves `elapsed_ms` at 0 when the app raises before
  sending; compute it in `finally` if still 0, or p50/p95 includes fake zeros.
- **`/api/health` does not touch the DB** (`main.py:49-51`); M5 health checks would report healthy
  with Postgres down.
- **`bench_tools.py` robustness**: `statistics.quantiles` raises on fewer than two samples
  (`:33-35`); if every create fails, the find/change/cancel lists are empty. The final
  `tool_invocations` query/delete (`:89-107`) races the middleware's post-response write.
- **Calculated number presented as measured**: `bookings.py:272` "about one in 900 million" is
  31⁶; say it is calculated, per the numbers rule in `CLAUDE.md`.
- **`FIELD_HINTS` live in `routes/tools.py`**, while `speech.py` claims to hold every spoken
  sentence. Move them or soften the docstring.
- **M5 security**: `/tools/*` bodies are buffered and written to `tool_invocations` before auth,
  including 401s. Once the tunnel is public, an unauthenticated caller can grow the table and force
  large bodies into memory. Cap/truncate the stored body, skip it on 401, and rate-limit at the
  edge.

## Decisions (settled)

1. **Schema-error contract** — `say` stays caller-facing; model guidance goes in `detail`
   (`ToolReply.detail`, omitted when absent).
2. **Stale idempotency key after a change** — keep the key; a key hit that no longer matches the
   requested slot or party size returns `ok: false`, `error: "already_booked_this_call"`, with the
   current booking.
3. **`weekday` without `date`** — rejected in `ChangeBookingRequest`.
4. **Invalid phone in change/cancel** — validated first; returns `invalid_phone`.
5. **Whether M1 is one commit or two** — two commits, split below.

## M1 commits

1. `Add the booking domain, schema and database guarantees` — models, migration, `restaurant`,
   `timeslots`, `bookings`, `speech`, `schemas`, `db`, `config`.
2. `Expose the booking tools with auth, latency logging and tests` — routes, security, invocations,
   main, tests, scripts, tooling, docs updates.

Both were made after the fixes above and a green suite. `.env` and `KICKOFF.md` are not committed.

## Subagent execution plan

The review items are executed by subagents in three waves, then committed by the main agent. No
subagent commits, pushes, or edits a file outside its assignment. Each wave ends with a report;
the main agent reads the changed files before starting the next wave.

### Gate: decisions before dispatch

These are the recommendations. Reply "approve" to use all of them, or amend any before the first
agent starts.

| # | Decision | Recommendation | Effect |
|---|---|---|---|
| 1 | `say` vs model guidance | Keep `say` caller-facing; add `detail` for the model | `ToolReply.detail`; generic `say` on schema errors |
| 2 | Stale idempotency key after a change | Keep the key; a key hit that no longer matches the requested slot/party returns `ok: false`, `error: "already_booked_this_call"`, with the current booking | No duplicate; no false "Booked" for the old slot |
| 3 | `weekday` without `date` | Reject in `ChangeBookingRequest` | A lone weekday can no longer no-op |
| 4 | Invalid phone in change/cancel | Validate first and return `invalid_phone` | Same sentence as find/create |
| 5 | M1 commit split | Two commits: domain, then tools/tests/tooling | Reviewable history |

Decision 5 can wait until Wave 3; decisions 1–4 must be settled before Agent B starts.

### Rules for every subagent

- Work in `C:\Users\cefalo\maitre-d`. Do not create a git worktree: M1 is untracked, so a new
  worktree would not contain it.
- Edit only the files assigned. If a fix needs another file, stop and report instead of editing it.
- No `git add`, `git commit`, `git checkout`, `git stash`, or `git reset`.
- Never print a secret or a full database URL. Report database names, not connection strings.
- Do not run Postgres-backed pytest unless the wave says so. Only one DB-backed pytest at a time.
- Do not change the exclusion constraint, the `idempotency_key()` inputs, `_queue_for_day`, or the
  name+phone access check. Those are the guarantees M1 exists to prove.
- Keep the house style: comments explain why, tests are named as sentences, UTF-8 without a BOM,
  LF line endings, no unrelated reformatting.
- Stop and report if a failure is outside the assigned scope. Do not "fix" M2/M5 items.

### Pre-flight (main agent, before Wave 1a)

1. `git status --short` and record it.
2. Zip `src/`, `tests/`, `scripts/`, `docs/`, `migrations/`, `alembic.ini`, `pyproject.toml`,
   `docker-compose.yml`, `.env.example` to
   `$env:TEMP\maitre-d-pre-review-fixes.zip`. This is the rollback if an agent goes wrong; do not
   include `.env`, `.pgdata`, `.venv` or `.git`.
3. Confirm Postgres is stopped or running; Wave 1a does not need it, Wave 1b does.

### Wave 1a — foundation: config, auth, test database (Agent A)

**Subagent:** `general`.
**Owns:** `src/maitre_d/config.py`, `src/maitre_d/security.py`, `tests/conftest.py`,
`scripts/create_databases.py` (new), `scripts/bench_tools.py`, `tests/test_config.py` (new),
`.env.example`.
**Does not touch:** `bookings.py`, `schemas.py`, `speech.py`, `routes/`, any other test file, docs.

Tasks:

1. `config.py`
   - Add `DEFAULT_DATABASE_URL` and `DEFAULT_TEST_DATABASE_URL` constants.
   - `database_url: str = DEFAULT_DATABASE_URL`.
   - `tool_bearer_secret: SecretStr | None = None`. The secret is optional because Alembic and
     `seed.py` need only the database URL; the auth boundary fails closed when it is unset.
   - `test_database_url: str = DEFAULT_TEST_DATABASE_URL`, so `.env` is the single source for the
     test database and `conftest.py` no longer reads `os.environ` directly.
   - Keep `extra="ignore"`. Do not log either URL.
2. `security.py`
   - Read the configured secret. If it is `None`, or its value is empty/whitespace, raise
     `HTTPException(500, detail="Tool authentication is not configured")` **before** parsing the
     header. This is the fail-closed fix for the empty-secret bug.
   - Otherwise keep the constant-time compare and the 401 + `WWW-Authenticate: Bearer` behaviour.
3. `tests/conftest.py`
   - Replace `os.environ.get("TEST_DATABASE_URL", ...)` with `Settings().test_database_url`.
   - Remove the now-unused `os` import. Leave the schema reset, Alembic upgrade and fixtures alone.
4. `scripts/create_databases.py` (new)
   - Read `Settings()`. For `database_url` and `test_database_url`, parse with
     `sqlalchemy.engine.make_url`, connect to the same server's `postgres` database with
     `isolation_level="AUTOCOMMIT"`, and `CREATE DATABASE` the target if it is missing.
   - Quote the identifier safely; never interpolate a URL or password into SQL.
   - Idempotent; prints only database names and "created"/"already exists". Exits non-zero with a
     clear message if the role lacks `CREATEDB`.
   - Docstring: run after `docker compose up -d` or on a fresh local Postgres, before the first
     `pytest`.
5. `scripts/bench_tools.py`
   - Making the secret optional means `settings.tool_bearer_secret.get_secret_value()` can now fail
     at runtime and under strict mypy. Add a small guard: if the secret is `None` or empty, exit
     with a clear message that `TOOL_BEARER_SECRET` must be set. Never print the value.
   - Keep the rest of the script unchanged.
6. `tests/test_config.py` (new, no database)
   - `Settings(_env_file=None)` succeeds with no `TOOL_BEARER_SECRET`, and
     `tool_bearer_secret is None`. Clear `TOOL_BEARER_SECRET` and `TEST_DATABASE_URL` with
     `monkeypatch.delenv(..., raising=False)` first, so a developer's shell cannot change the result.
   - `TEST_DATABASE_URL` set via `monkeypatch.setenv` is read into
     `Settings(_env_file=None).test_database_url`.
   - A tiny FastAPI app with `app.state.settings` and `Depends(require_tool_bearer)`:
     correct token → 200; missing and wrong token → 401 with `WWW-Authenticate: Bearer`;
     `None` secret + `Authorization: Bearer ` → 500, not 200; `""` secret + the same header → 500,
     not 200. Use `httpx.ASGITransport`; do not import the app fixture.
7. `.env.example`
   - Keep no real value. Add a comment that `TOOL_BEARER_SECRET` must be a long random value and
     that an empty value makes the tools return 500 (fail closed). Do not ship a working
     placeholder that someone might reuse.
8. Verify:
   - `uv run pytest tests/test_config.py -q`
   - `uv run ruff check src/maitre_d/config.py src/maitre_d/security.py tests/conftest.py tests/test_config.py scripts/create_databases.py scripts/bench_tools.py`
   - `uv run ruff format --check` on the same files.
   - `uv run mypy src/maitre_d/config.py src/maitre_d/security.py tests/test_config.py scripts/create_databases.py scripts/bench_tools.py`
   - Do **not** run the full suite or any DB-backed test.

Report: files changed, exact commands and results, any file outside the assignment that still
needs a change, and anything unresolved.

### Wave 1b — contract, wording, booking semantics (Agent B)

Run after Agent A reports, because it uses the fixed `conftest.py` and the test-database setting.

**Subagent:** `general`.
**Owns:** `src/maitre_d/speech.py`, `src/maitre_d/schemas.py`, `src/maitre_d/bookings.py`,
`src/maitre_d/routes/tools.py`, `docs/decisions.md`, `tests/helpers.py`,
`tests/test_speech_and_parsing.py`, `tests/test_timeslots.py`, `tests/test_idempotency.py`,
`tests/test_booking_tools.py`, `tests/test_tool_http.py`.
**Does not touch:** `config.py`, `security.py`, `conftest.py`, `main.py`, `invocations.py`,
`db.py`, `scripts/`, `CLAUDE.md`, `docs/plan.md`, `docs/wip.md`.

Tasks:

1. Caller-facing wording in `speech.py`
   - `weekday_mismatch`: "I have {date}, but you said {said}. Did you mean {suggestion}?" — no
     "the caller".
   - `outside_hours` with a pm guess: "We're not open then. Did you mean {time}?"
   - `invalid_phone`: "That phone number doesn't look complete. Could you give it again?"
   - `already_booked_this_call`: "I've already booked you {summary}. Would you like me to change
     that booking instead of making a second one?"
   - `changed`: "Done. The booking is now {summary}." (`booking_summary` already starts with
     "a table for", so this removes the double "for").
   - `unchanged`: "It's already booked: {summary}. Nothing needed changing."
   - `invalid_request`: generic and caller-facing, e.g. "Sorry, I didn't catch that. Could you say
     it again?" The field hints move to `detail`.
   - Scan the whole module for "caller", "This call", "ask for it again", and double "for".
   - Update the module docstring: it holds caller-facing sentences; model guidance lives in
     `routes/tools.py`.
2. `schemas.py`
   - Add `detail: str | None = None` to `ToolReply`, with a comment: `say` is read aloud; `detail`
     is for the model and is never spoken. `response_model_exclude_none=True` already keeps it out
     of successful replies.
   - `ChangeBookingRequest`: reject `weekday` without `date` with a clear message; keep rejecting
     `date` without `weekday`.
3. `bookings.py`
   - On a key hit, return `_booked_reply` only when the existing booking is confirmed and its
     `starts_at` and `party_size` match the requested start and party size. That is a true retry.
   - Otherwise return `ToolReply(ok=False, error="already_booked_this_call",
     booking=_booking_out(existing, today), say=speech.already_booked_this_call(...))`. This covers
     a booking moved earlier in the call. Cancelling clears the key, so a cancelled row should not
     reach this branch; add a comment saying so rather than inventing a second message.
   - Add a comment explaining why the key is kept after a change: it stops a late retry of the
     original create from booking the old slot twice.
   - Do not change `idempotency_key()` or the unique constraint.
   - If decision 4 is approved: in `change_booking` and `cancel_booking`, normalise the phone
     before `_verified_booking` and return `_invalid_phone()` when it cannot be parsed. Do not
     weaken the name+phone check.
4. `routes/tools.py`
   - `tool_validation_error`: `say=speech.invalid_request()` (no problems joined into it) and
     `detail="; ".join(problems)`.
   - Keep `FIELD_HINTS` here: they are model guidance, not speech. Add a hint only if the
     weekday/date validator's fallback message is unclear.
5. `docs/decisions.md`
   - Update #2 with the stale-key semantics and the retry distinction.
   - Update #4: schema failures return a generic caller-facing `say` plus `detail`.
   - Add #22, "A tool reply separates what the caller hears from what the model reads", with the
     decision, alternatives, and why. (21 already exists.)
6. Tests
   - `test_speech_and_parsing.py`: update exact strings; add `ChangeBookingRequest` rejection of
     `weekday` without `date`.
   - `test_timeslots.py`: update the exact weekday-mismatch and outside-hours strings.
   - `test_idempotency.py`: update exact strings; add the stale-key case — book Friday, change to
     Saturday, create Friday again returns `ok: false`, `error: "already_booked_this_call"`, the
     booking is Saturday, and no second row exists. Keep the true-retry test passing with the same
     reference.
   - `test_booking_tools.py`: replace `assert again == again | {...}` at `:208` with explicit
     asserts, including `booking["status"] == "cancelled"`; add change-to-a-new-date,
     party-size-only change, weekday-without-date over HTTP, `find_booking` with two bookings
     (plural `speech.found`), and `take_message` with an unparseable phone keeping the raw number.
   - `test_tool_http.py`: the malformed-call test asserts the generic `say` and that `detail`
     contains "date must be YYYY-MM-DD". Do not duplicate Agent A's empty-secret test.
   - `tests/helpers.py`: add a small `change_request` helper only if it removes repetition; keep
     existing builders unchanged.
7. Verify:
   - Pure: `uv run pytest tests/test_speech_and_parsing.py tests/test_timeslots.py -q`.
   - DB-backed (Postgres must be running, and Agent A's `conftest.py` must be in place):
     `uv run pytest tests/test_idempotency.py tests/test_booking_tools.py tests/test_tool_http.py -q`.
   - `uv run ruff check` and `uv run ruff format --check` on the owned files.
   - `uv run mypy` on the owned source files and the changed tests.
   - Do **not** run the full suite.

Report: files changed, exact commands and results, the new wording, any test that still fails and
why, and anything unresolved.

### Wave 2 — integration and verification (Agent C)

Run after Agent B reports. This agent may edit any file except `.env` and `KICKOFF.md`, but should
only touch files needed for integration and docs.

**Subagent:** `general`.
**Owns:** `tests/test_db.py` (new), `CLAUDE.md`, `docs/plan.md`, `docs/wip.md`, and integration
fixes that Wave 1a/1b missed. Do not rewrite Wave 1 work.

Tasks:

1. Start Postgres if needed, then run `uv run python scripts/create_databases.py` twice. The first
   run creates what is missing; the second must report "already exists" and change nothing. Record
   both outputs.
2. Run the full suite: `uv run pytest`. Record the exact count and time. If a Wave 1 change broke a
   test, fix it here and say which test and why.
3. Add `tests/test_db.py`: calling `sync_dining_tables` twice leaves exactly one row per table,
   with the labels and seat counts from `restaurant.TABLES`. Use the existing `engine` fixture.
4. Run and record:
   - `uv run ruff check .`
   - `uv run ruff format --check .`
   - `uv run mypy`
   - `uv run alembic check`
5. Fix `src/maitre_d/main.py`'s module docstring. Line 4 still says `Settings` requires
   `TOOL_BEARER_SECRET`, which is now optional. Reword it so it says importing the module must not
   need the environment; no behaviour change.
6. Update `CLAUDE.md`
   - Commands: `uv run python scripts/create_databases.py` before the first `pytest` and after
     `docker compose up -d` on a fresh volume.
   - Invariants: `say` is caller-facing; `detail` is model-facing and never spoken.
   - Layout: add `scripts/create_databases.py`, `tests/test_config.py`, `tests/test_db.py`.
   - Status: M1 is built, reviewed and fixed; still uncommitted until Wave 3.
7. Update `docs/plan.md`'s tool-reply contract to include optional `detail`, and say it is omitted
   when absent.
8. Update `docs/wip.md`
   - Tick the review items and the coverage list.
   - Replace "Decisions to settle" with the settled decisions.
   - Record the new measured test count/time and the lint/type/Alembic results, with conditions.
   - Keep the M2 checklist; move any remaining M2/M5 issue there rather than fixing it now.
9. Hygiene checks:
   - No BOM in any changed text file.
   - `git status --short`; `.env` and `KICKOFF.md` are not staged or tracked.
   - `git diff --check` (or the equivalent for untracked files) shows no whitespace errors.
10. Report: exact commands and results, files changed, any remaining failure, and the proposed
    commit contents. Do not commit.

### Wave 3 — commits (main agent, after user approval)

Only after Wave 2 is green and the user approves the diff.

**Commit 1 — `Add the booking domain, schema and database guarantees`**

`src/maitre_d/__init__.py`, `config.py`, `db.py`, `models.py`, `restaurant.py`, `timeslots.py`,
`bookings.py`, `speech.py`, `schemas.py`, `migrations/`, `alembic.ini`, `pyproject.toml`,
`uv.lock`, `.python-version`, `docker-compose.yml`, `.env.example`.

**Commit 2 — `Expose the booking tools with auth, latency logging and tests`**

`src/maitre_d/security.py`, `invocations.py`, `main.py`, `routes/`, `tests/`, `scripts/`,
`CLAUDE.md`, `docs/decisions.md`, `docs/plan.md`. Include `docs/wip.md` only if the user wants the
working notes in history; otherwise leave it untracked.

Before each commit: `git diff --cached --stat`, confirm no secret or `.env`, and run the checks
from Wave 2 once more if anything changed after them. Do not push unless asked.

### Acceptance gate before the first commit

- [ ] Full suite green, with the new count and time recorded.
- [ ] `ruff check .`, `ruff format --check .`, `mypy`, `alembic check` clean.
- [ ] `scripts/create_databases.py` is idempotent on a fresh database.
- [ ] `Settings()` works with no `TOOL_BEARER_SECRET`; an empty secret returns 500, not 200.
- [ ] `weekday` without `date` is rejected; the stale-key test passes.
- [ ] `say` has no third-person or schema language; `detail` carries the model hints.
- [ ] No `.env`, `KICKOFF.md`, `.pgdata`, or secret in the staged diff.

## Commands

```bash
# Local Postgres (portable binaries; data in .pgdata/, git-ignored)
PG="$LOCALAPPDATA/Programs/postgresql-17/bin"
"$PG/pg_ctl.exe" -D .pgdata -l .pgdata/server.log -w -t 60 start
"$PG/pg_ctl.exe" -D .pgdata -m fast stop

uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run alembic upgrade head
uv run alembic check
uv run python scripts/seed.py --demo
uv run uvicorn maitre_d.main:create_app --factory --reload --port 8000
uv run python scripts/bench_tools.py          # needs the server and TOOL_BEARER_SECRET
```

`uv run pytest` drops and recreates the `public` schema in `TEST_DATABASE_URL`. Never point it at
real data. The bench script writes and deletes rows tagged `bench_`.

## Next: M2 checklist

- [ ] Execute the subagent plan above: approve decisions 1–4, then Waves 1a, 1b and 2.
- [ ] Commit M1 in the two commits above after the acceptance gate passes.
- [ ] Re-check the ElevenLabs docs (the platform surface in `plan.md` §2 was verified on
  2026-10-25; versions move).
- [ ] `agent/` config as code, pushed with `elevenlabs agents push --dry-run` first.
- [ ] `knowledge/menu.md` + generated `knowledge/restaurant.md`, uploaded by `scripts/upload_kb.py`.
- [ ] `/api/session` returning a conversation token, never the API key.
- [ ] Cloudflared tunnel + `scripts/set_backend_host.py`; confirm
  `{{system__env_backend_host}}` works.
- [ ] Minimal React page with `ConversationProvider` + `useConversation` and "Talk to the agent".
- [ ] Answer the "to verify" list in `plan.md` §2 and record the results.
- [ ] First real voice call books a table that appears in Postgres.

## Hygiene

- `.env` and `KICKOFF.md` are git-ignored. Never print, log or commit a secret value.
- All data is fictional; phone numbers are in the Ofcom drama range 07700 900000–900999.
- Only measured numbers go in docs, comments or the README, with their conditions. The 88 tests /
  5.70s above is from this machine and this run.
- Write files as UTF-8 without a BOM; `.gitattributes` normalises line endings to LF.
