# Staff dashboard

One page: today's bookings, recent calls with a per-call timeline, caller messages, tool latency,
and a button that starts a web voice call. React, TypeScript (strict), Tailwind, TanStack Query.

```bash
npm ci
npm run dev        # :5173, proxies /api to the backend on :8000 (start it first)
npm run build      # typecheck, then dist/, which the backend serves at /
npm run lint && npm run typecheck && npm test
```

**API types are generated.** `openapi.json` comes from the backend
(`uv run python scripts/export_openapi.py`, from the repo root; `--check` fails if it is stale).
`npm run generate:types` turns it into `src/lib/api-schema.ts`. Never edit that file by hand.

**The Talk button** needs the backend running with ElevenLabs credentials and a pushed agent
(`/api/session` answers 503 otherwise), and a browser with a microphone. It is typechecked and
linted, but it cannot be tested without a real call, which spends voice minutes, so no test
covers it. The timeline logic (`src/lib/timeline.ts`) and the helpers around the button are tested.

Times are shown in London time. The dashboard has no login: the backend answers its `/api`
routes only to the local machine, so through a tunnel the page loads but shows no data.
