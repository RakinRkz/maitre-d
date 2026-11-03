# WIP — Maître D

State, open items and next actions. The design is in `docs/plan.md` and the reasons are in
`docs/decisions.md`. This file says what is true now and what is left, and is updated in the same
change as the code (see `CLAUDE.md`).

Last updated: 2026-11-03.

## Status

Everything in the plan is built and tested except the public deployment (a deliberate non-goal on
the Free plan). **Three real calls have been made (2026-11-03); the first worked end to end.** The agent,
tools, tests and post-call webhook are on the platform, and `backend_host` points at a quick tunnel
to this machine. The agent tests have never been run.

| Area | State |
|---|---|
| Booking domain, server tools, auth, timing log | built, tested |
| Post-call webhook, `calls` table | built, tested |
| `/api/session`, the dashboard read API, the local-only guard | built, tested |
| Dashboard frontend (React) | built, tested; the Talk button started the one real call but has no automated test |
| Agent as code, knowledge base, five agent tests | built, shape-tested, on the platform since 2026-11-02; the agent has taken one real call; the five tests have **never been run** |
| `setup_elevenlabs.py`, `create_webhook.py`, `run_agent_tests.py` | built, tested against a stateful fake; `setup`, `set_backend_host` and `create_webhook` run for real on 2026-11-02 (`run_agent_tests` has not been) |
| CI (`.github/workflows/ci.yml`) | written; passed its first GitHub run on 2026-11-03 (backend and frontend jobs, 383 and 68 tests) |
| README, LICENSE | written |

All of it is committed on `main`, in small commits, one concern each, and pushed to `origin`
(`github.com/RakinRkz/maitre-d`, a public repository). Until 2026-11-03 the remote held only the
first four commits.

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

## What the assistant cannot do for itself

The environment's permission layer refuses two kinds of action from the assistant: writing a
credential to the workspace secret store, and starting the tunnel. An explicit "go" in the
conversation did not change that (2026-11-02), and neither was worked around. The owner did both
in their own terminal, and the assistant then ran the other steps. If the tunnel restarts, the
owner starts it again; a quick tunnel gets a new hostname every time, so these are repeated:

1. Start the tunnel and note the `<hostname>.trycloudflare.com` it prints. Leave it running.
   PowerShell: `& "$env:LOCALAPPDATA\Programs\cloudflared\cloudflared.exe" tunnel --url http://localhost:8000`
   Git Bash: `"$LOCALAPPDATA/Programs/cloudflared/cloudflared.exe" tunnel --url http://localhost:8000`
   (PowerShell needs the leading `&` to run a quoted path.)
2. `uv run python scripts/set_backend_host.py <hostname>.trycloudflare.com`
3. `uv run python scripts/create_webhook.py <hostname>.trycloudflare.com`, then restart the backend
   (`uv run uvicorn maitre_d.main:create_app --factory --port 8000`) so it reads the new secret.

Calls are the owner's: they need a microphone and spend voice minutes.

## State of the ElevenLabs account

Read back through the API on 2026-11-02, after `setup_elevenlabs.py` (run by the owner),
`set_backend_host.py` and `create_webhook.py`:
- environment variables: `tool_bearer` (secret) and `backend_host`, pointing at the quick tunnel
  that was running that day (it stops resolving when the tunnel stops);
- six tools (`cancel_booking`, `change_booking`, `check_availability`, `create_booking`,
  `find_booking`, `take_message`);
- the agent `Saffron Table`, with all six tools and both knowledge-base pages attached;
- five agent tests;
- one workspace webhook, `Saffron Table post-call`, HMAC-signed, enabled, pointing at
  `/webhooks/elevenlabs` on the tunnel. The workspace's post-call settings have it for the
  `transcript` event only, with audio off.

`.env` holds the agent ID and the webhook secret (only whether they are filled in was checked).

Checked through the tunnel the same day, with the backend running: `/api/health` answers 200;
`/api/bookings` and `POST /api/session` answer 403 (the local-only guard sees real cloudflared
headers and refuses, which is what it is for); an unauthenticated tool call and an unsigned
webhook both answer 401; `/docs` answers 404; and one authenticated read-only `check_availability`
call returned `ok`. That last call came from this machine, not from the platform, so it shows
the bearer path and the tunnel work, not that the platform sends the header. Its log row was
removed so it does not count towards the latency panel.

## First live call, 2026-11-03

Started from the dashboard's Talk button (the platform records the source as the React SDK) with
the backend and Postgres on this machine, reached through the quick tunnel. Read back from the
stored call, the `tool_invocations` rows and the bookings table.

- **It worked.** 134 s; the agent ended it (`termination_reason`: "end_call tool was called");
  the platform's analysis marked it successful (score 100) and all three evaluation criteria passed
  (`resolved_or_handed_off`, `confirmed_before_booking`, `no_third_party_disclosure`). One booking
  exists, with one idempotency key.
- **The webhook worked on a real signature:** the call was stored 13 s after it ended, so the
  HMAC verification, the header scrub and the upsert have now seen a real payload.
- **Tool latency, one sample:** the platform's `tool_latency_secs` was 0.63 s for
  `check_availability` and 0.45 s and 0.52 s for the two `create_booking` calls it waited for; our
  middleware measured 3.8, 4.7 and 4.1 ms for the same requests. The platform also reported
  0.79 to 1.12 s for the model to write a tool request (six samples) and 0.36 to 1.34 s to start
  answering (sixteen). Three requests from one call on one network are not a distribution; the
  README says so.
- **Cost:** the platform's `cost` for the call is 1,619, and the account's usage went to 1,619 of
  10,000 credits (Free plan, resets monthly). That is about 12 credits per second for this call
  (134 s), so the plan's "15 minutes" is nearer 14 minutes at this rate. One call, so a rough guide.
  After two more calls (below) the account is at 4,463 of 10,000, so 5,537 are left.
- **Two more calls the same day** (05:25 and 05:27 UTC, 81 s and 149 s, costing 982 and 1,862
  credits) both ended with "Client disconnected" when the caller closed the page, and the
  platform's analysis marked both `resolved_or_handed_off` as failure. The first of the two never
  reached our server; the second made two `check_availability` requests, the first recorded by the
  platform as abandoned, as below. Not investigated further.
- **Found: the booking tool was abandoned and called three times** (decisions #29). The caller
  said "yes" while `create_booking` ran; the platform recorded "Tool execution was abandoned due
  to user input", and the agent called it twice more. Our log has three requests answered `ok` with
  one reference, and the database has one booking. The key did its job. Whether to stop the
  repeats (prompt wording, tool settings) is undecided and untested.
- **Answered from `plan.md` §2:** the conversation ID is filled by the platform and not by the
  model; `{{system__env_backend_host}}` works as a host; the bearer variable reference is sent;
  `tool_latency_secs` and `conversation_turn_metrics` are in real payloads (names in decisions #28).
- **Not answered:** what the model sees on a non-2xx status (every request got a 200); whether the
  platform retries on its own; whether the 180 s cap and the 30 s silence timeout are enforced (the
  call was shorter and never silent that long).
- **The README now has a screenshot of the bookings after the live calls** (the owner's own
  capture). It shows a first name and a made-up phone number outside the 07700 900000 to 900999
  range, which the repo's fictional-data rule asks for; the owner chose to use it. The row's name
  on the screen is not the name in the database for that booking, and this was not traced.

## Next actions

1. Decide what to do about the abandoned-and-repeated booking call (decisions #29): try a prompt
   or tool-setting change, then judge it with a second call (about 1,600 credits of the 5,537 left).
2. Still to answer from a call: whether `silence_end_call_timeout` counts while the agent speaks,
   whether the 180 s cap is enforced, and what the model sees on a non-2xx status. The last needs a
   deliberate failure, for example a wrong bearer on a copy of one tool, which costs a call.
3. If another call is made, record `tool_latency_secs` next to our server time again and add the
   samples to the README with their conditions. Announce the spend first.
4. Run **one** agent test with `--confirm-spend`, read `credits_used`, then decide how many of the
   other four to run. Put the measured cost in the README.
5. Record a demo call. The README has a bookings screenshot from the live calls; a call-timeline
   screenshot from a real call would replace the seeded one.
6. CI is green on its first GitHub run, and the repository is public. Its commits carry the author
   email configured in git; check that is the one you want visible.

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
- **The workflow wasn't machine-linted.** `actionlint` was blocked by the environment. It parses and
  ran green on GitHub once; GitHub annotated that `actions/checkout@v4`, `setup-uv@v5` and
  `setup-node@v4` target Node.js 20, which is deprecated, and that `ubuntu-latest` will move to
  Ubuntu 26. Neither failed the run.

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
