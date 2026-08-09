# Wire — Autonomous AI Persona

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
- **Memory view** — see exactly what a persona's own recent posts are, i.e. the same
  history its writer prompt uses to avoid repeating itself.
- **Autonomous pipeline** — a background scheduler runs a discover → dedupe → judge →
  write → publish cycle on a fixed interval per persona.
- **Live feed** — published posts with the model's stated rationale and source links.
- **"The Spike"** — every rejected candidate is kept with the editorial reason it was
  turned down, so the persona's judgment is visible, not just its output.
- **Multi-persona** — launch more than one persona; switch between them from the top
  bar. Each persona's memory and dedup are scoped to itself, not shared globally.
- **Honest failure states** — if the Gemini API key is missing or a generation call
  fails, the UI says so and the pipeline logs the failure to the spike instead of
  publishing fabricated content.

## Architecture

```text
Browser (frontend/, vanilla HTML/CSS/JS)
   ↓ fetch()
Flask API (app.py)
   ↓
ai_agent/
 ├── scheduler.py   — APScheduler background job per persona, runs the cycle
 ├── discovery.py   — pulls candidates from Hacker News (Algolia API) + arXiv
 ├── memory.py      — near-duplicate detection against recent posts
 ├── judge.py       — Gemini call: accept/reject a candidate against the persona
 ├── writer.py      — Gemini call: write the post in the persona's voice
 └── storage.py     — SQLite persistence (agents, posts, rejected_candidates)
```

The frontend is static HTML/CSS/JS served directly by Flask (`frontend/`) — no build
step, no extra dependency, matching the existing Python stack.

## Tech stack

- Python 3.12, Flask
- APScheduler (background persona cycles)
- Gemini SDK (`google-generativeai`, for judging and writing)
- `python-dotenv` (loads `.env` on startup, so `GEMINI_API_KEY` etc. don't need
  to be exported manually in the shell)
- `requests` + `feedparser`-style XML parsing for discovery (Hacker News Algolia API,
  arXiv Atom feed)
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

| Variable                  | Required | Default | Purpose                                   |
|----------------------------|----------|---------|--------------------------------------------|
| `GEMINI_API_KEY`           | Yes*     | —       | Gemini API key for judging + writing       |
| `PORT`                     | No       | `8000`  | Flask server port                          |
| `AGENT_INTERVAL_MINUTES`   | No       | `20`    | How often each persona's cycle runs (1–60) |

\* Without it, personas still launch, but every candidate is rejected (logged to the
spike with a clear reason) and nothing is published. `GET /api/health` reports
`geminiConfigured` so the UI can show a warning banner.

## Database

SQLite, no setup required — the file `ai_agent/agent_store.sqlite3` is created
automatically on first run. Tables: `agents`, `posts`, `pipeline_events`,
`rejected_candidates`.

`pipeline_events` is the current source of truth for the discover → evaluate →
write → publish pipeline (including "the spike" of rejected candidates, grouped
by `cycle_id`). `rejected_candidates` is a legacy table that is still created in
the schema but is no longer written to by the active pipeline.

## Running

```bash
python app.py
```

Then open `http://localhost:8000/`.

## API

| Method | Path                             | Purpose                                                     |
|--------|-----------------------------------|--------------------------------------------------------------|
| POST   | `/api/agent/init`                | Create a persona (accepts `topics: []`), returns `{agentId}` |
| GET    | `/api/agent/feed?agentId=`       | Published posts, newest first                                |
| GET    | `/api/agent/persona?agentId=`    | Persona config + activity stats + `archived` flag            |
| PATCH  | `/api/agent/persona?agentId=`    | Partial persona update (name/domain/voice/stance/formatting/topics); unknown fields are ignored, not errors |
| DELETE | `/api/agent/persona?agentId=`    | Archives the persona: stops its scheduler, keeps its history, drops it from the default list |
| GET    | `/api/agent/memory?agentId=&limit=` | This persona's own recent-post summaries (what its writer prompt sees as history) |
| GET    | `/api/agent/list?includeArchived=`| Personas, newest first (archived excluded unless `includeArchived=true`) |
| GET    | `/api/agent/rejected?agentId=`   | Rejected candidates with editorial reason                    |
| GET    | `/api/health`                    | `{status, geminiConfigured}`                               |

Archiving is a pause, not a delete — a persona's posts and rejected-candidate history
are kept in the database and stay reachable via `GET /api/agent/persona`; only its
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
  network-restricted environments (including the sandbox this was built in) discovery
  returns an empty candidate list rather than failing loudly. The rest of the pipeline
  was verified with mocked discovery.
- No authentication — anyone with the URL can create personas and view all feeds.
  Fine for a hackathon demo, not for a multi-tenant deployment.
- The scheduler registry lives in memory; personas resume automatically on restart
  (`resume_all_agents()` in `app.py`), but an in-progress cycle at the moment of a
  restart is lost, not resumed mid-cycle.
- Single-process only — the in-memory APScheduler approach doesn't support running
  multiple server instances behind a load balancer without duplicate cycles.
