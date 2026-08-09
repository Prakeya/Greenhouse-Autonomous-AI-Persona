# Greenhouse — Autonomous AI Persona

An autonomous editorial persona that runs on its own schedule: it discovers technical
stories from Hacker News and arXiv, judges each one against a configurable editorial
stance using Gemini, writes a post in the persona's voice, and publishes it to a feed —
without a human in the loop.

## Features

- **Persona setup** — name, domain/beat, topics/interests, voice, editorial stance,
  and formatting rules, all fed directly into the LLM prompts that judge and write.
- **Persona editing** — change any of the above after creation without recreating the
  persona; the running scheduler picks up the edit on its next cycle.
- **Archiving** — pause a persona (stops its scheduler) without losing its published
  history; archived personas drop off the default list but stay reachable directly.
- **Memory view** ("What it remembers") — see exactly what a persona's own recent
  posts are, i.e. the same history its writer prompt uses to avoid repeating itself.
- **Autonomous pipeline** — a background scheduler runs a discover → dedupe → judge →
  write → publish cycle on a fixed interval per persona.
- **Live feed** ("Letters sent") — published posts with the model's stated rationale
  and source links.
- **The compost heap** — every rejected candidate is kept with the editorial reason it
  was turned down, so the persona's judgment is visible, not just its output. Quota/
  rate-limit skips are tracked separately (`skipped`) so they're never mislabeled as a
  real editorial rejection.
- **Multi-persona** — launch more than one persona; switch between them from the top
  bar. Each persona's memory and dedup are scoped to itself, not shared globally.
- **Shared Gemini call budget** — a daily call budget (default 18, configurable) is
  tracked centrally and shown on the dashboard, since Gemini's quota is per API
  key/project, not per persona. Cycles stop calling the API once it's spent instead
  of repeatedly hitting a dead quota.
- **Honest failure states** — if the Gemini API key is missing, quota is exhausted, or
  a generation call fails, the UI says so (a banner on the dashboard) and the pipeline
  logs the real reason to the compost heap instead of publishing fabricated content.

## Architecture

```text
Browser (frontend/, vanilla HTML/CSS/JS)
   ↓ fetch()
Flask API (app.py)
   ↓
ai_agent/
 ├── scheduler.py    — APScheduler background job per persona; runs the cycle,
 │                      enforces the per-cycle candidate cap and daily call budget
 ├── discovery.py    — pulls candidates from Hacker News (Algolia API) + arXiv
 ├── memory.py       — near-duplicate detection against this persona's recent posts
 ├── judge.py        — Gemini call: accept/reject a candidate against the persona
 ├── writer.py       — Gemini call: write the post in the persona's voice
 ├── llm_errors.py   — classifies Gemini quota/rate-limit errors (429/RESOURCE_EXHAUSTED)
 │                      so they're handled distinctly from real editorial rejections
 └── storage.py      — SQLite persistence (agents, posts, pipeline_events,
                        daily llm_call_budget)
```

The frontend is static HTML/CSS/JS served directly by Flask (`frontend/`) — no build
step, no extra dependency, matching the existing Python stack.

## Tech stack

- Python 3.12, Flask
- APScheduler (background persona cycles)
- Gemini SDK (`google-genai` — the current, maintained SDK; the older
  `google-generativeai` package is deprecated and no longer used here)
- `python-dotenv` (loads `.env` on startup, so `GEMINI_API_KEY` etc. don't need
  to be exported manually in the shell)
- `requests` + `feedparser` for discovery (Hacker News Algolia API, arXiv Atom feed)
- SQLite (no external database needed)
- Frontend: vanilla HTML/CSS/JS, no framework or build tool

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate      # .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env
# edit .env and set GEMINI_API_KEY
```

## Environment variables

| Variable                       | Required | Default                                | Purpose                                                                 |
|---------------------------------|----------|-----------------------------------------|---------------------------------------------------------------------------|
| `GEMINI_API_KEY`                | Yes*     | —                                       | Gemini API key for judging + writing                                     |
| `GEMINI_MODEL`                  | No       | `gemini-2.5-flash`                     | Model used for both judge and writer calls                               |
| `PORT`                          | No       | `8000`                                 | Flask server port                                                        |
| `HOST`                          | No       | `0.0.0.0`                               | Flask bind host                                                          |
| `AGENT_INTERVAL_MINUTES`        | No       | `20`                                   | How often each persona's cycle runs (1–60)                               |
| `AGENT_DB_PATH`                 | No       | `ai_agent/agent_store.sqlite3`         | SQLite file location — **set this to a persistent volume path in production**, or personas/posts are lost on every restart/redeploy |
| `AGENT_MAX_CANDIDATES_PER_CYCLE`| No       | `5`                                     | Max candidates judged per cycle (caps Gemini calls per run)              |
| `GEMINI_DAILY_CALL_BUDGET`      | No       | `18`                                   | Shared daily cap on Gemini calls (judge + write, across all personas) — kept just under the free tier's 20/day; raise it once billing is enabled |
| `LOG_LEVEL`                     | No       | `INFO`                                 | Log verbosity                                                            |

\* Without it, personas still launch, but every candidate is rejected (logged to the
compost heap with a clear reason) and nothing is published. `GET /api/health` reports
`geminiConfigured` so the UI can show a warning banner.

## Gemini quota

Gemini's free tier caps `generate_content` calls (used by both judge and writer) at
20/day, shared across the whole API key/project — not per persona. This app tracks
a shared daily call budget (`GEMINI_DAILY_CALL_BUDGET`, default 18) and:

- Stops calling Gemini for the rest of a cycle the moment it gets a 429/quota error,
  and marks the whole day's budget as spent so other personas don't rediscover the
  same dead quota with a real request.
- Shows current usage (`used/budget`) directly on the dashboard, so you don't need
  to check Google's console to know whether the pipeline is paused for quota.
- Logs quota skips as a distinct `skipped` status, separate from genuine editorial
  rejections, so the compost heap only shows real "no" decisions.

For anything beyond light/demo use, enable billing on the Gemini API key — the free
tier is too small for a scheduler running every 20 minutes.

## Database

SQLite. By default the file `ai_agent/agent_store.sqlite3` is created automatically
on first run; override the path with `AGENT_DB_PATH` (recommended for any deployment
with ephemeral disk — most PaaS hosts wipe local files on restart/redeploy, which
otherwise causes personas to disappear). Tables: `agents`, `posts`, `pipeline_events`,
`rejected_candidates` (legacy, no longer written to), `llm_call_budget`.

`pipeline_events` is the source of truth for the discover → evaluate → write → publish
pipeline, including the compost heap (rejected candidates) and quota skips, grouped by
`cycle_id`.

## Running

```bash
python app.py
```

Then open `http://localhost:8000/`.

## API

| Method | Path                                | Purpose                                                                 |
|--------|--------------------------------------|---------------------------------------------------------------------------|
| POST   | `/api/agent/init`                   | Create a persona (accepts `topics: []`), returns `{agentId}`             |
| GET    | `/api/agent/feed?agentId=`         | Published posts, newest first                                            |
| GET    | `/api/agent/persona?agentId=`      | Persona config + activity stats + `archived` flag                        |
| PATCH  | `/api/agent/persona?agentId=`      | Partial persona update (name/domain/voice/stance/formatting/topics); unknown fields ignored, not errors |
| DELETE | `/api/agent/persona?agentId=`      | Archives the persona: stops its scheduler, keeps history, drops it from the default list |
| GET    | `/api/agent/memory?agentId=&limit=`| This persona's own recent-post summaries (what its writer prompt sees as history) |
| GET    | `/api/agent/list?includeArchived=` | Personas, newest first (archived excluded unless `includeArchived=true`) |
| GET    | `/api/agent/rejected?agentId=`     | Compost heap: rejected candidates with editorial reason                  |
| GET    | `/api/agent/pipeline/counts?agentId=`| Per-stage counts (discover/evaluate/write/publish) plus today's Gemini call-budget usage |
| GET    | `/api/agent/pipeline/<stage>?agentId=&limit=`| Raw event log for one stage (`discover`/`evaluate`/`write`/`publish`) |
| GET    | `/api/health`                       | `{status, geminiConfigured}`                                              |

Archiving is a pause, not a delete — a persona's posts and rejected-candidate history
stay in the database and remain reachable via `GET /api/agent/persona`; only its
background scheduler stops and it drops out of the default `/api/agent/list` view.

## Testing

```bash
pip install pytest
pytest tests/ -v
```

`scripts/test_integration.py` is a live smoke test against a running server (needs
`GEMINI_API_KEY` and outbound network access to Hacker News / arXiv):

```bash
python app.py &
python scripts/test_integration.py
```

## Known limitations

- Discovery depends on outbound access to `hn.algolia.com` and `export.arxiv.org`; in
  network-restricted environments discovery returns an empty candidate list rather
  than failing loudly.
- No authentication — anyone with the URL can create personas and view all feeds.
  Fine for a demo, not for a multi-tenant deployment.
- The scheduler registry lives in memory; personas resume automatically on restart
  (`resume_all_agents()` in `app.py`), but an in-progress cycle at the moment of a
  restart is lost, not resumed mid-cycle.
- Single-process only — the in-memory APScheduler approach doesn't support running
  multiple server instances behind a load balancer without duplicate cycles.
- The Gemini free tier's daily quota is genuinely small for an "autonomous" scheduler;
  see the **Gemini quota** section above before expecting continuous publishing.
