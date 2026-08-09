# Changelog

A running record of what's been done on this project, checkpoint by checkpoint.
This file ships inside every zip so the history travels with the code.

---

## Checkpoint 7 — Phase 2 frontend: edit, archive, memory, topics UI
**Status:** Complete, tested, logged, visually verified with real screenshots

Wires the checkpoint 6 backend (PATCH/DELETE/memory/topics) into `frontend/app.js` —
closes the gap explicitly flagged at the end of checkpoint 6.

- **Setup form:** added a "Topics / interests" field (comma-separated text input,
  optional) alongside the existing domain field, parsed into a list and sent as
  `topics` on `POST /api/agent/init`.
- **Masthead:** split into a read view and an edit view. Read view now shows a
  `Topics:` byline line when the persona has any, plus two new buttons — "Edit
  persona" and "Archive persona".
- **Edit persona:** clicking it swaps the masthead for an inline form (same field
  styling as setup, pre-filled from the currently loaded persona, including topics
  joined back into a comma-separated string). Submits `PATCH /api/agent/persona`
  with only the editable fields; on success re-fetches and re-renders the dashboard,
  on failure shows an inline error and re-enables the form. "Cancel" discards and
  returns to the read view without a network call. Polling is paused while the edit
  form is open (via the existing `editing` flag threaded through `renderDashboard`)
  so a background refresh can't clobber text mid-edit.
- **Archive persona:** confirm() dialog explaining it's a pause (scheduler stops,
  history kept, still reachable by URL) not a delete, then calls
  `DELETE /api/agent/persona`. On success, re-fetches the (now-filtered) agent list
  and either switches to another existing persona or falls back to the setup
  screen if none remain.
- **New "What it remembers" panel:** added below the existing "compost heap" panel
  in the sidebar, same visual treatment, fetches `GET /api/agent/memory?agentId=`
  and lists this persona's own recent post titles/summaries — the same data its
  writer prompt sees, made visible per the Phase 2 spec requirement.
- `renderDashboard` now fetches `memory` alongside the existing persona/feed/
  rejected/list/health calls (one added `Promise.all` member), and the polling loop
  does the same, so the memory panel stays live.
- Added `apiPatch` / `apiDelete` fetch helpers alongside the existing `apiGet` /
  `apiPost`, same error-shaping behavior (structured `{error:{code,message}}`
  responses surfaced as `Error.message`, `.status` set for 404 handling upstream).
- CSS: `.masthead-actions` (button row) and `.btn-danger` (archive button, uses the
  existing `--rust` token already used elsewhere for errors/rejections — no new
  palette introduced).
- **Verified with a real headless browser this pass** (Playwright/Chromium was
  available in this sandbox instance, unlike checkpoints 4–6): full click-through
  flow — fill setup form with topics → dashboard renders topics line + memory panel
  → open edit form, confirm prefill (including topics) → change voice and topics →
  save → confirm byline updates → archive with confirm dialog auto-accepted →
  confirm fallback to setup screen when no personas remain. Screenshotted at both
  1200px desktop and 390px mobile viewports; mobile confirmed the new buttons wrap
  correctly and the edit form's field stack matches the setup form's existing
  mobile layout. Server log cross-checked against every UI action in the same
  session (persona create with topics, PATCH with the exact fields changed, archive
  with scheduler-stop confirmation) — UI and logs agree.
- Logging: no frontend logging exists or was added (browser-side, not a Python
  logger) — the backend logging added in checkpoint 6 already covers every action
  the new UI triggers; this pass confirmed that end-to-end via the browser instead
  of only curl.

Files touched (new): none.
Files touched (edited): `frontend/app.js`, `frontend/styles.css`.

## Checkpoint 6 — Phase 3 backend APIs, agent-scoped memory, naming fix
**Status:** Complete, tested, logged

Closes the real gap flagged at the end of checkpoint 5: persona editing, archiving,
a memory-listing endpoint, and topics/interests management didn't exist on the
backend, so nothing could be wired to them.

- Added `PATCH /api/agent/persona?agentId=` — partial persona update (name, domain,
  voice, stance, formatting, topics). Unknown fields in the body are dropped and
  logged, not rejected, so a client sending extra keys doesn't 422. Refreshes the
  running scheduler's in-memory persona so the next cycle uses the edit without a
  restart.
- Added `DELETE /api/agent/persona?agentId=` — archives a persona (stops its
  scheduler, flips an `archived` flag) rather than hard-deleting it. Posts and
  rejected-candidate history are kept; the persona still resolves via
  `GET /api/agent/persona` (now returns `archived: true/false`) but drops out of
  `GET /api/agent/list` unless `?includeArchived=true` is passed.
- Added `GET /api/agent/memory?agentId=&limit=` — a persona's own recent-post
  summaries, i.e. exactly what its writer prompt sees as history.
- Added `topics` (list of strings) to the persona shape, accepted at
  `POST /api/agent/init` and editable via the new PATCH route. Threaded into both
  `judge.py` and `writer.py` system prompts as a "specific topics/interests to
  prioritize" line alongside the existing `domain` string.
- `agents` table migrated in place (`ALTER TABLE ... ADD COLUMN archived`), guarded
  so it's a no-op on a DB that already has the column — safe on both fresh and
  existing SQLite files.
- **Real bug found and fixed while wiring the memory endpoint:** `memory.is_duplicate`
  and `writer.generate_post`'s history lookup were both *global* across all
  personas, not scoped to the calling agent. In a multi-persona setup, one
  persona's coverage of a topic could silently suppress a different persona
  covering the same topic, and one persona's writer could see another persona's
  post history. Added `get_recent_posts_for_agent` /
  `get_recent_post_summaries_for_agent` and switched `is_duplicate` and
  `generate_post` to use them; `scheduler.run_cycle` now passes `agent_id` through
  to both. Covered by a new test (`test_get_memory_is_scoped_per_agent`) that
  creates two agents and asserts one's memory never contains the other's post.
- Logging: every new code path logs at INFO (persona update with which fields
  changed and which were ignored, archive with scheduler-stop confirmation, the
  dedup-scope fix at DEBUG) through the existing `logging_config.py` setup — no new
  logging system introduced, same rotating file + console handlers as checkpoint 5.
  Verified live: started the server, ran init → PATCH → GET memory → list → DELETE →
  list against a running instance, confirmed every step appears in `logs/app.log`
  with the right agent id and fields, and confirmed 404/422 error paths return the
  right status codes without crashing the process.
- Added 6 new tests to `tests/test_agent.py` (partial update, unknown-field
  handling, unknown-agent 404-equivalent, archive hides from list, archive on
  unknown agent, memory scoping) — `pytest` now 7/7 passing, up from 1/1.
- `README.md` API table updated with the three new routes and an explicit note
  that archiving is a pause (history kept), not a delete.
- **Naming fix:** this project's spec calls for
  `checkpoint-1-ui.zip` → `checkpoint-2-integration.zip` → `checkpoint-3-ai.zip` →
  `-final.zip`, but checkpoints 2 through 5 shipped as `-2-ui`, `-3-ui`, `-4-ui`,
  `-5-ui` — an iterative-UI-pass naming scheme that drifted from the spec's
  phase-based one. This checkpoint ships as `checkpoint-6-phase3.zip` to name what
  it actually contains (Phase 3 backend work), breaking the `-N-ui` pattern on
  purpose. See `PROJECT_AUDIT.md` for the full phase-to-checkpoint mapping.

Files touched (new): none.
Files touched (edited): `app.py`, `ai_agent/agent.py`, `ai_agent/storage.py`,
`ai_agent/memory.py`, `ai_agent/writer.py`, `ai_agent/judge.py`,
`ai_agent/scheduler.py`, `tests/test_agent.py`, `README.md`.

## Checkpoint 5 — Real logging system
**Status:** Complete, tested

- Added `ai_agent/logging_config.py`: a central `configure_logging()` that
  sets up a rotating file handler (`logs/app.log`, 2MB × 3 backups) plus a
  console handler, level controlled by the `LOG_LEVEL` env var (default
  `INFO`). Called once at the top of `app.py`, before other imports run.
- Added real tracing through the actual pipeline, not just failures:
  - `scheduler.py` — cycle start/end, candidates discovered, each
    judgment (accept/reject + reason), each post published.
  - `discovery.py` — per-source fetch counts, and the two previously
    *silent* exception swallows (`fetch_hn_candidates`,
    `fetch_arxiv_candidates`) now log a warning with the real error.
  - `judge.py` — the LLM-call failure path was silently swallowed before;
    now logs the exception.
  - `writer.py` — logs missing API key and empty-model-output failures.
  - `agent.py` — logs persona creation, resume-on-restart, and the
    first-run cycle failure that used to be a bare `except: pass`.
  - `storage.py` — debug-level logs on post/rejection writes.
  - `app.py` — logs persona creation from the API layer, startup resume
    count, server start, and 404s on `/api/*`.
- Verified end-to-end: started the server, created a persona (which runs
  an immediate cycle), and confirmed `logs/app.log` captured the full
  trace — including a real, previously-undocumented-in-real-time finding:
  both Hacker News and arXiv return 403 in this sandbox, now visible as a
  `WARNING` the moment it happens instead of only in `PROJECT_AUDIT.md`.
- Added `.gitignore` (`logs/`, `*.sqlite3`, `__pycache__/`, `.env`) so log
  files and the local DB don't get shipped or committed.
- `pytest` 1/1 still passing; both `app.py` and every `ai_agent/*.py`
  module compile clean.

Files touched (new): `ai_agent/logging_config.py`, `.gitignore`.
Files touched (edited): `app.py`, `ai_agent/agent.py`,
`ai_agent/scheduler.py`, `ai_agent/discovery.py`, `ai_agent/judge.py`,
`ai_agent/writer.py`, `ai_agent/storage.py`.

## Checkpoint 3 — UI self-verification
**Status:** Complete, visually verified

- Found a headless Chromium binary already present in the sandbox and used it
  (via Playwright) to actually screenshot the app for the first time —
  setup screen, populated dashboard, empty-state dashboard, and a 390px
  mobile view.
- Caught and fixed a copy inconsistency the screenshots surfaced: the stat
  strip said "Spiked" while the section below had been renamed "The compost
  heap" — both now say "Composted."
- Confirmed responsive behavior (pipeline cards collapse to 2×2 on mobile,
  topbar pill wraps), empty/dashed states, and the multi-persona switcher
  all render correctly.
- Seeded and then removed test data; re-ran `pytest` clean before packaging.

Files touched: `frontend/app.js` (one label fix), `CHANGELOG.md` (new).

## Checkpoint 2 — Garden/letters UI redesign
**Status:** Complete, tested

- Rebuilt the visual identity from a dark newsroom theme to a warm
  parchment/garden theme, following the user's `gardenletters.online`
  reference image.
- Palette: parchment cream, sage green, kraft tan, dusty pink, mauve.
- Type: Fraunces (italic serif headlines), Inter (body/UI), Special Elite
  (typewriter face for stamps and labels).
- Pipeline strip rebuilt as four colored cards (Discover → Evaluate →
  Write → Publish), each with its own icon and a numbered corner badge,
  mirroring the four-card layout in the reference image.
- Published posts now render as opened letters: rounded paper card with a
  faint torn-envelope-flap top edge and a typewriter-style postmark date.
- Top bar rebuilt as a pill-shaped browser-chrome bar with a flower brand
  mark.
- Copy retuned to the metaphor ("Plant persona," "Letters sent," "The
  compost heap") without losing clarity about what each control does.
- Verified: app boots clean, static assets serve 200, `/api/health`
  responds, `pytest` 1/1 still passing (backend untouched).

Files touched: `frontend/styles.css` (full rewrite), `frontend/app.js`
(pipeline markup + copy), `frontend/index.html` (font links).

## Checkpoint 1 — Backend fixes + first frontend build
**Status:** Complete, tested

- Fixed unmockable `run_cycle` import in `agent.py` that was breaking the
  one existing test.
- Fixed missing `flask` dependency in `requirements.txt`.
- Fixed `writer.py` silently fabricating fake posts on LLM failure — now
  fails closed and logs to the spike.
- Added persona/list/rejected/health endpoints.
- Added `resume_all_agents()` so personas survive a server restart.
- Built the first full frontend from scratch (newsroom/editorial design,
  vanilla HTML/CSS/JS, served by Flask).
- Wrote `PROJECT_AUDIT.md`, `TEST_REPORT.md`, `README.md`.
- All 6 endpoints tested live via curl (init/feed/persona/list/rejected/
  health) — correct 201/200/404/422s.
- Frontend verified against live API responses: setup form, dashboard,
  feed clippings, spike panel, persona switcher, loading/empty/error
  states, auto-poll.
- Reject-path and accept/publish-path both verified end-to-end (discovery
  itself mocked — HN/arXiv blocked by this sandbox's network allowlist,
  everything downstream real).
- `pytest`: 1/1 passing, verified against a clean venv built only from
  `requirements.txt`.

**Known issues carried forward:**
- Discovery itself is untested against real HN/arXiv in this sandbox
  (network-restricted) — code unchanged from original, only downstream
  wired up and tested.
- No auth (documented as a known limitation, out of scope).
- Orphaned `hackathon/` folder (docs-only, no source) left untouched.

Files changed: `app.py` (rewritten), `ai_agent/agent.py`,
`ai_agent/scheduler.py`, `ai_agent/storage.py`, `ai_agent/writer.py`,
`requirements.txt`. New: `frontend/index.html`, `frontend/app.js`,
`frontend/styles.css`, `README.md`, `TEST_REPORT.md`, `PROJECT_AUDIT.md`.
