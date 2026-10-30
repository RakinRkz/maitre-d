# WIP — Maître D

State, open items and next actions. The design is in `docs/plan.md` and the reasons are in
`docs/decisions.md`. This file says what is true now and what is left, and is updated in the same
change as the code (see `CLAUDE.md`).

Last updated: 2026-10-30.

## Status

Everything in the plan is built and tested except the public deployment (a deliberate non-goal on
the Free plan). **No live call has been made**, and the platform side is only partly set up.

| Area | State |
|---|---|
| Booking domain, server tools, auth, timing log | built, tested |
| Post-call webhook, `calls` table | built, tested |
| `/api/session`, the dashboard read API, the local-only guard | built, tested |
| Dashboard frontend (React) | built, tested; the Talk button is typechecked only |
| Agent as code, knowledge base, five agent tests | built, shape-tested; **never run** |
| `setup_elevenlabs.py`, `create_webhook.py`, `run_agent_tests.py` | built, tested against a stateful fake; `setup` run for real as far as the secret |
| CI (`.github/workflows/ci.yml`) | written; its commands pass locally; **has not run on GitHub** |
| README, LICENSE | written |

All of it is committed on `main`, in small commits, one concern each. Nothing has been pushed, and
there is no GitHub remote yet.

## Verified on 2026-10-30

Windows 11, local Postgres 17.11, pytest through httpx's ASGI transport, schema rebuilt from the
migrations each run.

- `uv run pytest` → **383 passed in 13.90 s**. At the start of this work, 104 passed in 5.46 s.
- `ruff check`, `ruff format --check`, `mypy` (52 source files), `alembic check` → clean.
- `generate_knowledge.py --check`, `sync_agent_prompt.py --check`, `export_openapi.py --check` → in sync.
- Frontend: `typecheck`, `lint` (zero warnings), `npm test` → **68 passed**, `build`; regenerating
  `api-schema.ts` leaves its hash unchanged. The drift gate was checked in the failing direction:
  adding a field to a backend response model makes `export_openapi.py --check` exit 1.
- `uv sync --locked` succeeds and leaves `uv.lock` unchanged (CI uses it).
- **A security review** of the pending changes (a read-only reviewer, then an independent
  false-positive filter) found nothing it rated at or above its reporting bar. It raised one
  Medium (DNS rebinding past the local-only guard), which the filter rated 4/10; it was fixed
  anyway as hardening (decisions #24). It also noticed, below its bar, that a non-JSON body
  from a stranger got a friendly 200 instead of a 401 and was stored; that was reproduced by
  a test and fixed (decisions #19).
- The dashboard was looked at in a browser against the seeded demo data: no console errors, no
  horizontal overflow at phone width, dark mode applies, and the call timeline renders.
- **Mutation checks** (break the code one way, run the tests, restore). Webhook, session, local-only
  guard, invocation log, header scrubbing, static serving, provisioning, evals, seed data and agent
  config each had a set run against them; every deliberate bug is now caught. Along the way this
  found tests that were too weak and they were fixed: a `Content-Length` pre-check made redundant by
  the streaming cap (deleted), a logging test whose fixture the validation error truncated away, a
  blank-API-key case, a header list the test read from the module it was testing, a `.env` comment
  check that nothing needed (deleted), and an "any versus all" polling rule. The frontend agent ran
  19 breaks against `timeline.ts` and found and fixed one real bug.

## Blocked on the owner

Two steps were refused by the environment's permission layer and are not worked around. Both are
needed to finish the platform setup. Allow them (or run the commands yourself), in this order:

1. **Write the tool bearer to the workspace secret store.**
   `uv run python scripts/setup_elevenlabs.py` does it. If you would rather not let a script hold the
   bearer, create a workspace secret named `tool_bearer` whose value is `Bearer <TOOL_BEARER_SECRET>`,
   then a **secret-type** environment variable labelled `tool_bearer` pointing at it, and run
   `setup_elevenlabs.py --skip-secret`. Until the variable exists, creating the tools fails with
   `Environment variable with label 'tool_bearer' not found` (seen on 2026-10-30).
2. **Publish the backend through a tunnel.**
   `cloudflared tunnel --url http://localhost:8000`, then `set_backend_host.py <host>` and
   `create_webhook.py <host>`, then restart the backend so it reads the new webhook secret.

After that, the first real call is the owner's: it needs a microphone and spends voice minutes.

## State of the ElevenLabs account

Created by `setup_elevenlabs.py --skip-secret` on 2026-10-30: the `backend_host` environment
variable (value `tunnel-not-started.invalid`, which can never resolve) and the knowledge-base
documents `menu.md` and `restaurant.md`. A second run reused both documents (the platform returns
uploaded text verbatim, so the re-run check works). Nothing else exists: no secret, tool, agent,
test or webhook. `.env` still holds placeholder values for `ELEVENLABS_AGENT_ID` and
`ELEVENLABS_WEBHOOK_SECRET`; the scripts replace them when they succeed.

## Next actions

1. Unblock the two steps above, then `setup_elevenlabs.py`; confirm each tool is created.
2. Answer the "to verify" list in `plan.md` §2 from the first real call, and record the results:
   whether the tool header's variable reference is sent, what the model sees on a non-2xx status,
   the names and unit of `conversation_turn_metrics`, whether `silence_end_call_timeout` counts
   while the agent speaks, and whether the 180 s cap is enforced.
3. Make one or two calls, each under three minutes (announce the spend first). Record
   `tool_latency_secs` next to our server time and the call count; put them in the README with
   the conditions.
4. Run **one** agent test with `--confirm-spend`, read `credits_used`, then decide how many of the
   other four to run. Put the measured cost in the README.
5. Record a demo call and take a dashboard screenshot from the real call (the one in the README is
   seeded demo data).
6. Create the GitHub remote and push (the owner's call), then fix whatever the first CI run finds.
   Before publishing the history, note that the commits carry the author email configured in git;
   check it is the one you want public.

## Open findings

- **Last seating is defined twice.** `knowledge.last_seating()` re-derives "closing time minus the
  sitting length", which `timeslots.candidate_starts()` and `_fits_service()` also write inline. A
  test brute-forces agreement, so they can't drift silently, but one function used by all three
  would be the single definition.
- **`GET /api/calls` default limit (50) is untested**; explicit limits and the 1–200 bounds are.
- **Tool path logging has no test for the INFO lines.** They read as safe (tool, status, outcome,
  timing, a booking reference, a table label); only the webhook, session and failed-write paths are
  pinned.
- **No rate limiting on `/tools/*`.** Refusals no longer cost a row and bodies are capped, but a
  stranger can still make the server do work. Rate-limit at the edge if this is ever deployed.
- **Invalid phone in change/cancel** returns `not_found`, not `invalid_phone` as find/create do (no
  security loss); `FIELD_HINTS` mislabel length errors; `sync_dining_tables` upserts but never
  removes a table; `/api/health` doesn't touch the database; a 500 raised before a response is sent
  is recorded with 0 ms (the latency query ignores non-200 rows for this reason).
- **The workflow wasn't machine-linted.** `actionlint` was blocked by the environment; the YAML
  parses and every command in it was run locally.

## Choices made that the owner may want to change

- **MIT licence, in the owner's name** (`LICENSE`, `pyproject.toml`). Change both if not wanted.
- **Menu content** (`knowledge/menu.md`): sulphites on the sea bass (beurre blanc is made with wine)
  and a line saying feta and paneer use vegetarian rennet. Both are invented facts about a fictional
  menu.
- **Voice and model are the CLI template's defaults** (`cjVigY5qzO86Huf0OWal`, `gemini-2.5-flash`),
  neither chosen nor measured. A British voice is a free change.
- **Limits** (decisions #26): 180 s call cap, 30 s silence timeout, one concurrent call, ten
  conversations a day, no bursting. A booking that runs past three minutes is cut off.
- **`set_backend_host.py` defaults to `production`** (decisions #12), because it is the only
  environment on the account.
- **Provisioning by script, not CLI** (decisions #27), and `--skip-secret` for anyone who'd rather
  hold the secret themselves.
- **TypeScript 5.9.3**, because typescript-eslint and openapi-typescript cap it (decisions #28).

## Hygiene

- `.env` and `KICKOFF.md` are git-ignored. Never print, log or commit a secret value; `setup` and
  `create_webhook` write secrets straight to `.env` and print none.
- All data is fictional; phone numbers are in the Ofcom drama range 07700 900000–900999.
- Only measured numbers go in docs, comments or the README, with their conditions. The 383 tests /
  13.90 s above is from this machine and this run.
- Write files as UTF-8 without a BOM; `.gitattributes` normalises line endings to LF.
