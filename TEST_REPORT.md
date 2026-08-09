# TEST_REPORT.md

> **Note (added during the Gemini migration pass):** this report documents a
> specific historical test run (checkpoint 7), when the AI provider was still
> Claude/Anthropic (`anthropic` SDK, `ANTHROPIC_API_KEY`). The current
> application uses Gemini (`google-generativeai`, `GEMINI_API_KEY`) and the
> `geminiConfigured` health field — see `tests/test_gemini_provider.py` and
> CHANGELOG.md for the migration and its own test coverage. The report below
> is left as originally written for historical accuracy.

Regenerated at checkpoint 7, against the actual current codebase (the version
before checkpoint 6 was written before that checkpoint's logging changes landed
and never reflected them — see CHANGELOG.md for both entries).

## Environment
Python 3.12.3, dependencies installed from `requirements.txt` via
`pip install --break-system-packages`. Tested in a sandboxed container with outbound
network restricted to a domain allowlist that does **not** include `hn.algolia.com`
or `export.arxiv.org` — real discovery could not be exercised here; downstream
pipeline stages were verified with a mocked scheduler cycle instead.
`ANTHROPIC_API_KEY` used for live-server testing was an intentionally invalid
placeholder — sufficient to verify wiring and error handling, not real content
quality.

## Build
PASS — `pip install -r requirements.txt` succeeds; `python app.py` starts and serves
on the configured port. Confirmed the SQLite migration (`archived` column added to
`agents`) runs cleanly on startup and logs a single INFO line, `init_db: migrated
agents table, added archived column`.

## Type Check
N/A — project has no type-checking config (no mypy/pyright setup); not added, per
"don't introduce unnecessary dependencies."

## Tests
PASS — `pytest tests/ -v` → **7/7 passed** (`tests/test_agent.py`), up from 1/1 at
checkpoint 5. The 6 new tests cover the Phase 3 work directly: partial persona
update, unknown-field handling on update, update against an unknown agent, archive
hiding a persona from the default list, archive against an unknown agent, and
memory being scoped per-agent (two agents created, asserts one's memory never
contains the other's post — this is the regression test for the cross-persona
memory bug described below).

## Backend
PASS — all endpoints tested with curl against a running server, in sequence, in one
session (see Logging section for the corresponding log lines):
- `POST /api/agent/init` → 201, returns `agentId`; accepts a `topics` list, persisted
  and echoed back on the following persona read
- `GET /api/agent/feed?agentId=` → 200
- `GET /api/agent/feed` (no agentId) → 422 with structured error
- `GET /api/agent/persona?agentId=<unknown>` → 404 with structured error
- `GET /api/agent/persona?agentId=<real>` → 200, now includes `archived: false`
- `PATCH /api/agent/persona?agentId=<real>` → 200, partial update applied; unknown
  field in the same request body dropped silently (logged, not errored)
- `PATCH /api/agent/persona?agentId=<unknown>` → 404
- `PATCH /api/agent/persona` with empty JSON body → 422
- `DELETE /api/agent/persona?agentId=<real>` → 200, `{"archived": true}`
- `DELETE /api/agent/persona?agentId=<unknown>` → 404
- `GET /api/agent/persona?agentId=<archived>` → 200, `archived: true` (still
  reachable directly — archiving does not delete)
- `GET /api/agent/list` → excludes the archived persona by default
- `GET /api/agent/list?includeArchived=true` → includes it
- `GET /api/agent/memory?agentId=` → 200, `{"memory": [...]}`, scoped to that agent
- `GET /api/agent/rejected?agentId=` → 200
- `GET /api/health` → 200, `{"status":"ok","anthropicConfigured":bool}`
- Unknown non-API route → falls back to serving the SPA shell (200)

## AI Integration
PASS — `judge.py` and `writer.py` both make real `anthropic` SDK calls (not
simulated); confirmed `judge.py` correctly rejects and logs a reason when the
Claude call fails (e.g. invalid key). `writer.py` raises on failure rather than
fabricating a templated post; the scheduler catches that, logs it to
`rejected_candidates` with a clear reason, and skips publishing — confirmed via the
reject-path with an invalid key. The accept/publish path (a real successful
generation) was verified with a mocked LLM call, not a live valid-key call, since no
valid key is available in this environment — this is a testing-environment
limitation, not a code gap; the call path itself
(`Anthropic(api_key=...).messages.create(...)`) is unchanged and real. Persona
`topics` are now threaded into both prompts as a "specific topics/interests to
prioritize" line.

## Database
PASS — SQLite auto-creates on first run; `agents`, `posts`, `rejected_candidates`
tables verified via direct queries; the new `archived` column confirmed both on a
fresh DB (created with the column from the start) and via the migration path
(existing DB gets `ALTER TABLE ... ADD COLUMN`, logged, idempotent — running init_db
twice does not error or duplicate the column). A persona created before a server
restart is still present and its scheduler resumes automatically after restart
(archived personas are correctly *not* resumed, since `resume_all_agents` uses the
same `include_archived=False` default as the list endpoint).

## Cross-persona memory bug (found and fixed this pass)
`memory.is_duplicate` and `writer.generate_post`'s history lookup were both global
across all agents, not scoped to the calling persona — in a multi-persona setup, one
persona's coverage could silently block a different persona from covering the same
topic, and one persona's writer prompt could see another persona's post history as
if it were its own. Fixed by adding agent-scoped storage functions and threading
`agent_id` through `is_duplicate` and `generate_post`; regression-tested in
`test_get_memory_is_scoped_per_agent`.

## Logging
PASS — verified live, not just by reading the code: started the server with
`LOG_LEVEL=INFO`, ran init → persona read → PATCH → memory read → list → DELETE →
list → persona read against a running instance in one shell session, then read back
`logs/app.log`. Every operation appears with the right agent id: persona creation
(with topics), the scheduler's first cycle (including the two expected discovery 403
warnings), the PATCH with which fields changed and which were dropped, the archive
with explicit scheduler-stop confirmation. No new logging system was introduced —
this uses the same rotating-file + console setup from checkpoint 5
(`ai_agent/logging_config.py`).

## Responsive UI
PASS — re-verified this pass (checkpoint 7) with a real headless browser
(Playwright/Chromium, available in this sandbox instance). Full click-through
flow exercised and screenshotted at 1200px desktop and 390px mobile: setup form
with the new topics field, dashboard with the new Edit/Archive buttons and "What
it remembers" panel, the inline edit form pre-filled from live data, the updated
dashboard after a save, and the fallback-to-setup screen after archiving the only
persona. Mobile confirmed the new buttons wrap correctly and the edit form's field
stack matches the setup form's existing mobile layout — no fixed-width overflow.
The PATCH/DELETE/memory endpoints and the `topics` field, called out as unwired at
checkpoint 6, are now wired into `frontend/app.js`.

## End-to-End Flow
PASS — full cycle verified directly against the pipeline (discovery mocked, since
outbound access to Hacker News/arXiv is blocked in this sandbox):
1. Reject-path: fake candidate → judge fails closed (bad key) → logged to
   `rejected_candidates` with reason → visible via `/api/agent/rejected`.
2. Accept-path: fake candidate → judge mocked to accept → writer mocked to generate
   → post saved → visible via `/api/agent/feed`.
3. Server restart mid-session: previously created persona and its post survive and
   are still served correctly; archived personas stay archived across a restart.
4. New this pass — edit/archive/memory cycle: create → patch → verify change
   persisted → archive → verify hidden from list but still directly readable →
   verify its scheduler actually stopped (no further cycle log lines after archive).
5. New at checkpoint 7 — same cycle driven through the actual browser UI instead of
   curl: setup form (with topics) → dashboard → Edit persona → change fields → Save
   → dashboard reflects the change → Archive persona → confirm dialog → fallback to
   setup screen. Server log for the session cross-checked against each UI action.

## Known Issues
- Discovery (`ai_agent/discovery.py`) could not be tested against the real Hacker
  News / arXiv endpoints in this sandbox due to network egress restrictions.
  Unchanged from the original implementation.
- No authentication on any endpoint, including the new PATCH/DELETE routes — anyone
  with the URL can edit or archive any persona. Acceptable for a hackathon demo;
  flagged as a limitation, not fixed, since auth wasn't part of the original scope.
- The accept/publish path for AI integration has only been verified with a mocked
  LLM response, never a live valid-key call, since no valid Anthropic key is
  available in this test environment.
- Phase 2 (frontend) now exposes persona editing, archiving, memory view, and
  topics management as of checkpoint 7 — see CHANGELOG.md. Not exercised against a
  live/valid Claude key end-to-end through the UI (same limitation as the AI
  Integration section above); wiring and error paths were verified with the invalid
  test key.

## Final Status
Backend Phase 3 (persona edit, archive, memory endpoint, topics field, checkpoint 6)
and its frontend wiring (checkpoint 7) are both complete and tested: 7/7 pytest,
a full live curl sequence with logging verified (checkpoint 6), and a full
Playwright click-through with real screenshots at desktop and mobile viewports,
cross-checked against the server log line-by-line (checkpoint 7). One real
cross-persona memory bug was found and fixed as a direct result of building the
memory endpoint. No known gaps remain against the Phase 3 / Phase 2 spec items
raised at the end of checkpoint 5, beyond the environment-inherent ones listed
above (no real discovery access, no live-key AI verification, no auth).
