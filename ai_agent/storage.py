import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("AGENT_DB_PATH") or os.path.join(os.path.dirname(__file__), "agent_store.sqlite3")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                persona TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Migration: older DBs (pre-Phase 3) won't have this column yet. SQLite has
        # no "ADD COLUMN IF NOT EXISTS", so probe for it and add it if missing —
        # safe to run on every startup, on both fresh and existing databases.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(agents)").fetchall()}
        if "archived" not in existing_cols:
            conn.execute("ALTER TABLE agents ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            logger.info("init_db: migrated agents table, added archived column")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                url TEXT NOT NULL,
                published TEXT NOT NULL,
                createdAt TEXT NOT NULL,
                text TEXT NOT NULL,
                rationale TEXT NOT NULL,
                sources TEXT NOT NULL,
                FOREIGN KEY(agent_id) REFERENCES agents(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rejected_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                summary TEXT,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(agent_id) REFERENCES agents(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                source_url TEXT,
                title TEXT,
                snippet TEXT,
                content TEXT,
                reason TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_call_budget (
                day TEXT PRIMARY KEY,
                calls_used INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_agent_created ON posts(agent_id, createdAt DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rejections_agent ON rejected_candidates(agent_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_events_persona_stage "
            "ON pipeline_events(persona_id, stage, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_events_cycle ON pipeline_events(cycle_id)"
        )
        conn.commit()
    finally:
        conn.close()


def save_agent(agent_id: str, persona: Dict[str, Any]) -> None:
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO agents(id, persona, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET persona=excluded.persona, updated_at=excluded.updated_at
            """,
            (agent_id, json.dumps(persona), now, now),
        )
        conn.commit()
    finally:
        conn.close()


def update_agent_persona(agent_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merges `updates` into the stored persona dict (partial update) and bumps
    updated_at. Returns the full merged persona, or {} if the agent doesn't exist."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT persona FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            logger.warning("update_agent_persona: no such agent_id=%s", agent_id)
            return {}
        persona = json.loads(row["persona"])
        persona.update(updates)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE agents SET persona = ?, updated_at = ? WHERE id = ?",
            (json.dumps(persona), now, agent_id),
        )
        conn.commit()
        logger.info("update_agent_persona: agent_id=%s fields=%s", agent_id, sorted(updates.keys()))
        return persona
    finally:
        conn.close()


def set_agent_archived(agent_id: str, archived: bool) -> bool:
    """Marks an agent archived/unarchived. Returns True if a row was updated."""
    init_db()
    conn = _connect()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "UPDATE agents SET archived = ?, updated_at = ? WHERE id = ?",
            (1 if archived else 0, now, agent_id),
        )
        conn.commit()
        updated = cursor.rowcount > 0
        logger.info("set_agent_archived: agent_id=%s archived=%s updated=%s", agent_id, archived, updated)
        return updated
    finally:
        conn.close()


def get_agent(agent_id: str) -> Dict[str, Any]:
    init_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT persona FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            return {}
        return json.loads(row["persona"])
    finally:
        conn.close()


def get_agent_full(agent_id: str) -> Dict[str, Any]:
    """Returns persona plus metadata (created/updated timestamps, post count) for one agent."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, persona, created_at, updated_at, archived FROM agents WHERE id = ?",
            (agent_id,),
        ).fetchone()
        if row is None:
            return {}
        post_count_row = conn.execute(
            "SELECT COUNT(*) AS c FROM posts WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        # Sourced from the pipeline event log (stage=evaluate, status=rejected) rather
        # than the legacy rejected_candidates table, which is no longer written to.
        rejected_count_row = conn.execute(
            "SELECT COUNT(*) AS c FROM pipeline_events WHERE persona_id = ? AND stage = 'evaluate' AND status = 'rejected'",
            (agent_id,),
        ).fetchone()
        return {
            "id": row["id"],
            "persona": json.loads(row["persona"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "archived": bool(row["archived"]),
            "postCount": post_count_row["c"] if post_count_row else 0,
            "rejectedCount": rejected_count_row["c"] if rejected_count_row else 0,
        }
    finally:
        conn.close()


def list_agents(include_archived: bool = False) -> List[Dict[str, Any]]:
    """Returns agents newest-first with basic persona + activity info, for a
    landing/switcher UI. Archived agents are excluded unless include_archived=True."""
    init_db()
    conn = _connect()
    try:
        query = "SELECT id, persona, created_at, updated_at, archived FROM agents"
        if not include_archived:
            query += " WHERE archived = 0"
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            post_count_row = conn.execute(
                "SELECT COUNT(*) AS c FROM posts WHERE agent_id = ?", (row["id"],)
            ).fetchone()
            result.append(
                {
                    "id": row["id"],
                    "persona": json.loads(row["persona"]),
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                    "archived": bool(row["archived"]),
                    "postCount": post_count_row["c"] if post_count_row else 0,
                }
            )
        return result
    finally:
        conn.close()


def save_post(post: Dict[str, Any]) -> None:
    init_db()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO posts(
                id, agent_id, title, summary, url, published, createdAt, text, rationale, sources
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post["id"],
                post["agent_id"],
                post["title"],
                post.get("summary") or post.get("title"),
                post["url"],
                post.get("published") or post.get("createdAt"),
                post.get("createdAt") or post.get("published"),
                post["text"],
                post.get("rationale") or "",
                json.dumps(post.get("sources", [])),
            ),
        )
        conn.commit()
        logger.debug("save_post: stored post id=%s agent_id=%s", post["id"], post["agent_id"])
    finally:
        conn.close()


def get_posts_for_agent(agent_id: str) -> List[Dict[str, Any]]:
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT id, agent_id, title, summary, url, published, createdAt, text, rationale, sources
            FROM posts WHERE agent_id = ? ORDER BY createdAt DESC
            """,
            (agent_id,),
        ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "id": row["id"],
                    "agent_id": row["agent_id"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "url": row["url"],
                    "published": row["published"],
                    "createdAt": row["createdAt"],
                    "text": row["text"],
                    "rationale": row["rationale"],
                    "sources": json.loads(row["sources"] or "[]"),
                }
            )
        return result
    finally:
        conn.close()


def log_rejected_candidate(agent_id: str, candidate: Dict[str, Any], reason: str) -> None:
    init_db()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO rejected_candidates(agent_id, title, url, summary, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id,
                candidate.get("title") or "Untitled",
                candidate.get("url"),
                candidate.get("summary"),
                reason,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        logger.debug("log_rejected_candidate: agent_id=%s title=%r", agent_id, candidate.get("title"))
    finally:
        conn.close()


# NOTE: get_recent_posts / get_recent_post_summaries below are GLOBAL (all agents),
# kept only for the /api/agent/list-style admin views. Per-agent dedup and the
# /api/agent/memory endpoint use the *_for_agent variants above instead — the
# global versions were a real cross-persona memory-leak bug when used for dedup
# (memory.is_duplicate previously called the global one), fixed in this pass.
def get_recent_posts(limit: int = 20) -> List[Dict[str, Any]]:
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT id, agent_id, title, summary, url, published, createdAt, text, rationale, sources
            FROM posts ORDER BY createdAt DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "id": row["id"],
                    "agent_id": row["agent_id"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "url": row["url"],
                    "published": row["published"],
                    "createdAt": row["createdAt"],
                    "text": row["text"],
                    "rationale": row["rationale"],
                    "sources": json.loads(row["sources"] or "[]"),
                }
            )
        return result
    finally:
        conn.close()


def get_rejected_for_agent(agent_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT title, url, summary, reason, created_at
            FROM rejected_candidates WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?
            """,
            (agent_id, limit),
        ).fetchall()
        return [
            {
                "title": row["title"],
                "url": row["url"],
                "summary": row["summary"],
                "reason": row["reason"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_recent_posts_for_agent(agent_id: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Agent-scoped version of get_recent_posts, for per-persona dedup/memory."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT id, agent_id, title, summary, url, published, createdAt, text, rationale, sources
            FROM posts WHERE agent_id = ? ORDER BY createdAt DESC LIMIT ?
            """,
            (agent_id, limit),
        ).fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "id": row["id"],
                    "agent_id": row["agent_id"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "url": row["url"],
                    "published": row["published"],
                    "createdAt": row["createdAt"],
                    "text": row["text"],
                    "rationale": row["rationale"],
                    "sources": json.loads(row["sources"] or "[]"),
                }
            )
        return result
    finally:
        conn.close()


def get_recent_post_summaries_for_agent(agent_id: str, limit: int = 15) -> List[Dict[str, str]]:
    """Agent-scoped version of get_recent_post_summaries, for the /api/agent/memory
    endpoint and for feeding a persona's own history back into its prompts."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT title, summary, createdAt FROM posts WHERE agent_id = ? ORDER BY createdAt DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        result: List[Dict[str, str]] = []
        for row in rows:
            result.append(
                {
                    "title": row["title"],
                    "summary": row["summary"] or row["title"],
                    "createdAt": row["createdAt"],
                }
            )
        return result
    finally:
        conn.close()


def log_pipeline_event(
    persona_id: str,
    cycle_id: str,
    stage: str,
    status: str,
    source_url: str = None,
    title: str = None,
    snippet: str = None,
    content: str = None,
    reason: str = None,
    metadata: Dict[str, Any] = None,
) -> None:
    """Writes one durable pipeline_events row. This is the single source of truth
    for discover/evaluate/write/publish activity — never let a logging failure
    take down the calling pipeline stage; callers should wrap this defensively
    for the same reason the old log_rejected_candidate calls were wrapped."""
    init_db()
    conn = _connect()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO pipeline_events(
                persona_id, cycle_id, stage, status, source_url, title, snippet,
                content, reason, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                persona_id,
                cycle_id,
                stage,
                status,
                source_url,
                title,
                snippet,
                content,
                reason,
                json.dumps(metadata) if metadata is not None else None,
                now,
            ),
        )
        # "Last activity" on the dashboard reads agents.updated_at, which was
        # previously only touched on persona create/edit — so it never moved
        # during actual discover/evaluate/write/publish cycles. Bump it here too.
        conn.execute("UPDATE agents SET updated_at = ? WHERE id = ?", (now, persona_id))
        conn.commit()
        logger.debug(
            "log_pipeline_event: persona_id=%s cycle_id=%s stage=%s status=%s",
            persona_id, cycle_id, stage, status,
        )
    finally:
        conn.close()


def _row_to_pipeline_event(row: sqlite3.Row) -> Dict[str, Any]:
    metadata_raw = row["metadata_json"]
    return {
        "id": row["id"],
        "personaId": row["persona_id"],
        "cycleId": row["cycle_id"],
        "stage": row["stage"],
        "status": row["status"],
        "sourceUrl": row["source_url"],
        "title": row["title"],
        "snippet": row["snippet"],
        "content": row["content"],
        "reason": row["reason"],
        "metadata": json.loads(metadata_raw) if metadata_raw else {},
        "createdAt": row["created_at"],
    }


def get_pipeline_events(agent_id: str, stage: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Newest-first events for one persona/stage, for the dedicated stage pages."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT id, persona_id, cycle_id, stage, status, source_url, title, snippet,
                   content, reason, metadata_json, created_at
            FROM pipeline_events
            WHERE persona_id = ? AND stage = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (agent_id, stage, limit),
        ).fetchall()
        return [_row_to_pipeline_event(row) for row in rows]
    finally:
        conn.close()


def get_pipeline_event_counts(agent_id: str) -> Dict[str, Any]:
    """Per-stage counts (plus an accepted/rejected split for evaluate) used by the
    dashboard cards, and the id of the most recent cycle for a quick preview."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT stage, status, COUNT(*) AS c
            FROM pipeline_events WHERE persona_id = ?
            GROUP BY stage, status
            """,
            (agent_id,),
        ).fetchall()
        counts: Dict[str, Any] = {
            "discover": 0,
            "evaluate": {"accepted": 0, "rejected": 0, "failed": 0, "skipped": 0},
            "write": 0,
            "publish": 0,
        }
        for row in rows:
            stage, status, c = row["stage"], row["status"], row["c"]
            if stage == "evaluate" and status in counts["evaluate"]:
                counts["evaluate"][status] += c
            elif stage == "discover":
                counts["discover"] += c
            elif stage == "write" and status == "drafted":
                counts["write"] += c
            elif stage == "publish" and status == "published":
                counts["publish"] += c
        latest_row = conn.execute(
            "SELECT cycle_id, created_at FROM pipeline_events WHERE persona_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
        counts["latestCycleId"] = latest_row["cycle_id"] if latest_row else None
        counts["latestEventAt"] = latest_row["created_at"] if latest_row else None
        return counts
    finally:
        conn.close()


def get_rejected_evaluations_for_agent(agent_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Compost-heap view: rejected evaluate-stage events, in the same shape the
    frontend already expects from the legacy rejected_candidates table (title,
    url, summary, reason, createdAt) so no frontend change was needed for it."""
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT title, source_url, snippet, reason, created_at
            FROM pipeline_events
            WHERE persona_id = ? AND stage = 'evaluate' AND status = 'rejected'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ).fetchall()
        return [
            {
                "title": row["title"] or "Untitled",
                "url": row["source_url"],
                "summary": row["snippet"],
                "reason": row["reason"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_recent_post_summaries(limit: int = 15) -> List[Dict[str, str]]:
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT title, summary FROM posts ORDER BY createdAt DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result: List[Dict[str, str]] = []
        for row in rows:
            result.append(
                {
                    "title": row["title"],
                    "summary": row["summary"] or row["title"],
                }
            )
        return result
    finally:
        conn.close()


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_llm_calls_used_today() -> int:
    """Calls used against the shared Gemini quota today (UTC). The budget is
    tracked globally, not per-persona, because Gemini's rate/quota limits are
    per API key/project — every persona sharing that key draws from the same
    pool."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT calls_used FROM llm_call_budget WHERE day = ?", (_today_str(),)
        ).fetchone()
        return row["calls_used"] if row else 0
    finally:
        conn.close()


def increment_llm_calls_used(n: int = 1) -> int:
    """Records n Gemini calls against today's budget. Returns the new total."""
    init_db()
    conn = _connect()
    day = _today_str()
    try:
        conn.execute(
            """
            INSERT INTO llm_call_budget(day, calls_used) VALUES (?, ?)
            ON CONFLICT(day) DO UPDATE SET calls_used = calls_used + excluded.calls_used
            """,
            (day, n),
        )
        conn.commit()
        row = conn.execute(
            "SELECT calls_used FROM llm_call_budget WHERE day = ?", (day,)
        ).fetchone()
        return row["calls_used"] if row else n
    finally:
        conn.close()


def mark_llm_budget_exhausted_today() -> None:
    """Called the moment Gemini itself reports 429/RESOURCE_EXHAUSTED, so every
    persona's next cycle today skips calling the API at all instead of each
    one independently rediscovering the same dead quota with a real request."""
    init_db()
    conn = _connect()
    day = _today_str()
    try:
        conn.execute(
            """
            INSERT INTO llm_call_budget(day, calls_used) VALUES (?, 999999)
            ON CONFLICT(day) DO UPDATE SET calls_used = 999999
            """,
            (day,),
        )
        conn.commit()
    finally:
        conn.close()
