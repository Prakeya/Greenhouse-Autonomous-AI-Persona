# PROJECT_AUDIT.md — Phase 0 Audit

> **Note (added during the Gemini migration pass):** this is a historical,
> point-in-time audit. Where it describes the AI provider as Claude/Anthropic
> (`anthropic` SDK, `ANTHROPIC_API_KEY`), that reflects the codebase *at the
> time of this audit only*. The current application uses Gemini
> (`google-generativeai`, `GEMINI_API_KEY`) — see README.md and CHANGELOG.md
> for the migration. The rest of this document is left as originally written.

## 1. Current architecture

The uploaded archive actually contains **two separate, unrelated backend implementations** and **no frontend at all**.

### A. Root project (`/`) — Flask "AI Persona" MVP
- **Framework:** Flask (`app.py`), single-process, in-memory agent registry.
- **AI:** `ai_agent/` package — `discovery.py` (Hacker News + arXiv polling), `judge.py` (Claude-based accept/reject), `writer.py` (Claude-based post generation with a hardcoded fallback if the API call fails), `memory.py` (dedup via title similarity), `scheduler.py` (APScheduler background job per agent).
- **Storage:** raw `sqlite3` in `ai_agent/storage.py` (`agents`, `posts`, `rejected_candidates` tables) — file `ai_agent/agent_store.sqlite3`.
- **API surface:** exactly two routes — `POST /api/agent/init`, `GET /api/agent/feed`.
- **Tests:** `tests/test_agent.py` (1 unit test, mocks the scheduler), `scripts/test_integration.py` (live HTTP smoke test), `scripts/demo_run.py` (manual demo).
- **This is a real, runnable, self-contained system.** It discovers topics, judges them with Claude, writes posts with Claude, stores them, and serves them over HTTP.

### B. `hackathon/hackathon/backend/` — FastAPI backend, **source code missing**
- `README.md` and `BACKEND_HANDOVER.md` describe a considerably more sophisticated backend: FastAPI + SQLAlchemy, four models (`Agent`, `Topic`, `Post`, `Memory`), an `AI interfaces` contract layer (`app/services/ai_interfaces.py`) meant for a separate "AI team" to implement, a `PublishingService`, an `AgentWorker`/`AutonomousScheduler` for the discover→evaluate→generate→validate→publish loop, structured logging, and a documented 48-test pytest suite.
- **However, the actual `app/` source directory is not present in this upload** — only the docs, `.env.example`, `.gitignore`, an empty `.pytest_cache`, and a populated `app.db` (SQLite file with `agents`, `topics`, `memories`, `posts` tables, confirming this backend genuinely existed and ran at some point) made it into the zip.
- I cannot inspect, run, fix, or extend this backend's code because **it isn't here**. Nothing under `hackathon/` can be treated as "existing functionality to preserve" — I have no source to preserve.

### C. Frontend — does not exist
- There is no `package.json`, no `src/`, no `.tsx`/`.jsx`, no HTML templates, nothing resembling a UI anywhere in the archive.
- Note this contradicts your usual stack (React/Express/tRPC/Drizzle) — this project is pure Python, and there is nothing to "finish" on the frontend; it would be built from scratch.

## 2. What already works
- The root Flask + `ai_agent` system works end-to-end in principle: init a persona → background scheduler ticks → discovers candidates from HN/arXiv → Claude judges → Claude writes → SQLite stores → feed endpoint serves newest-first posts with rationale + sources.
- Real Claude API integration (`anthropic` SDK) for both judging and writing, gated correctly behind `ANTHROPIC_API_KEY`.
- Reasonable resilience: try/except around each pipeline stage so one bad candidate doesn't kill a cycle; a fallback post template if the LLM call fails (though this **silently fabricates content** rather than surfacing an error — flagged below as a concern per your task rules on not faking AI output).

## 3. What is incomplete / missing
- No frontend of any kind.
- The more fully-specified backend (`hackathon/hackathon/backend`) is undeployable — its code is absent.
- Root Flask app has no persona-listing, memory-viewing, topic-management, or rejected-candidate-viewing endpoints — only init + feed.
- No auth, no multi-user support, no CORS config, no `.env` actually created (only `.env.example`).
- `writer.py`'s silent fallback content on LLM failure conflicts with your task instructions ("Do not silently fall back to fake AI responses").

## 4. Build/test status (root Flask app only — this is the only runnable code)
Not yet executed — pending your direction, since which backend to build against changes the answer.

## 5. Integration gaps
- No frontend to integrate, so all of Phases 1–2 of your instructions are "build from scratch," not "connect existing UI."
- Two incompatible backend data models exist (simple SQLite posts table vs. the documented Agent/Topic/Post/Memory relational schema) — they cannot both be "the" backend.

## 6. Recommended path
Given the above, I'd rather confirm direction with you than guess and burn effort building against the wrong backend or reconstructing FastAPI code from documentation (which would violate "do not rebuild from scratch" / "preserve existing functionality," since I'd be inventing code, not restoring it).

## 7. Checkpoint naming note (added checkpoint 6)
The spec's naming scheme is phase-based: `checkpoint-1-ui.zip`,
`checkpoint-2-integration.zip`, `checkpoint-3-ai.zip`, `-final.zip`. What actually
shipped drifted into an iterative-pass scheme instead:

| Zip as shipped              | What it actually contains                                    |
|------------------------------|----------------------------------------------------------------|
| `checkpoint-1-ui.zip`         | Phase 0 audit (this file)                                      |
| `checkpoint-2-ui.zip`         | Garden/letters visual redesign                                 |
| `checkpoint-3-ui.zip`         | UI self-verification (Playwright screenshots)                  |
| `checkpoint-4-ui.zip`         | Frontend↔backend wiring pass                                   |
| `checkpoint-5-ui.zip`         | Logging system                                                 |
| `checkpoint-6-phase3.zip`     | Phase 3 backend APIs (edit/archive/memory/topics), test report regen, this naming fix |

From checkpoint 6 onward, zip names describe their actual contents rather than
continuing the `-N-ui` pattern.
