import logging
import os
import uuid
from typing import Any, Dict, List

from . import scheduler as scheduler_mod
from .scheduler import set_agent_registry, start_scheduler, stop_scheduler
from .storage import (
    get_agent_full,
    get_llm_calls_used_today,
    get_pipeline_event_counts,
    get_pipeline_events,
    get_posts_for_agent,
    get_recent_post_summaries_for_agent,
    get_rejected_evaluations_for_agent,
    list_agents as storage_list_agents,
    save_agent,
    set_agent_archived,
    update_agent_persona,
)

PIPELINE_STAGES = {"discover", "evaluate", "write", "publish"}

logger = logging.getLogger(__name__)

# Fields an editor is allowed to change on an existing persona via PATCH.
# "topics" is a list of strings (interests/beat refinements beyond the single
# `domain` string); everything else mirrors the fields accepted at creation.
EDITABLE_PERSONA_FIELDS = {"name", "domain", "voice", "stance", "formatting", "topics"}


def _scheduler_interval_minutes() -> int:
    try:
        value = int(os.getenv("AGENT_INTERVAL_MINUTES") or os.getenv("AGENT_SCHEDULER_INTERVAL_MINUTES") or "20")
        return max(1, min(value, 60))
    except ValueError:
        return 20


def init_agent(persona: dict) -> str:
    """
    Initializes and starts the agent scheduler.
    """
    agent_id = str(uuid.uuid4())
    save_agent(agent_id, persona)
    set_agent_registry(agent_id, persona)
    start_scheduler(agent_id, interval_minutes=_scheduler_interval_minutes())
    logger.info("init_agent: created agent_id=%s name=%s", agent_id, persona.get("name"))
    try:
        scheduler_mod.run_cycle(agent_id)
    except Exception:
        logger.exception("init_agent: first-run cycle failed for agent_id=%s (scheduled runs will retry)", agent_id)
    return agent_id


def resume_all_agents() -> int:
    """
    Re-registers and re-starts schedulers for every persona already saved in the
    database. Needed because the scheduler registry lives in memory and is lost
    on process restart, while personas persist in SQLite. Returns count resumed.
    """
    resumed = 0
    for agent in storage_list_agents():
        agent_id = agent.get("id")
        persona = agent.get("persona") or {}
        if not agent_id:
            continue
        set_agent_registry(agent_id, persona)
        start_scheduler(agent_id, interval_minutes=_scheduler_interval_minutes())
        resumed += 1
    logger.info("resume_all_agents: resumed %d agent(s)", resumed)
    return resumed


def get_persona(agent_id: str) -> Dict[str, Any]:
    """
    Returns persona config plus activity metadata for one agent, or {} if unknown.
    """
    return get_agent_full(agent_id)


def list_agents(include_archived: bool = False) -> List[Dict[str, Any]]:
    """
    Returns known agents newest-first, for a persona switcher / landing view.
    Archived agents are excluded by default.
    """
    return storage_list_agents(include_archived=include_archived)


def update_persona(agent_id: str, updates: dict) -> Dict[str, Any]:
    """
    Applies a partial update to an existing persona (name/domain/voice/stance/
    formatting/topics). Unknown fields are ignored rather than erroring, so a
    client sending extra keys doesn't blow up the request. Returns {} if the
    agent doesn't exist. The running scheduler's in-memory registry is refreshed
    too, so the next cycle uses the new persona without a restart.
    """
    if not isinstance(updates, dict):
        logger.warning("update_persona: rejected non-dict updates for agent_id=%s", agent_id)
        return {}
    filtered = {k: v for k, v in updates.items() if k in EDITABLE_PERSONA_FIELDS}
    dropped = set(updates.keys()) - EDITABLE_PERSONA_FIELDS
    if dropped:
        logger.info("update_persona: agent_id=%s ignored unknown fields=%s", agent_id, sorted(dropped))
    if not filtered:
        logger.info("update_persona: agent_id=%s had no editable fields in request", agent_id)
        return get_agent_full(agent_id)

    merged_persona = update_agent_persona(agent_id, filtered)
    if merged_persona:
        set_agent_registry(agent_id, merged_persona)
        logger.info("update_persona: agent_id=%s updated fields=%s", agent_id, sorted(filtered.keys()))
    return get_agent_full(agent_id)


def archive_agent(agent_id: str) -> bool:
    """
    Archives a persona: stops its background scheduler (no more autonomous
    cycles) and marks it archived so it drops out of the default list view.
    Existing posts/history are kept, not deleted — this is a pause, not a purge.
    Returns True if the agent existed and was archived.
    """
    updated = set_agent_archived(agent_id, True)
    if updated:
        stop_scheduler(agent_id)
        logger.info("archive_agent: agent_id=%s archived and scheduler stopped", agent_id)
    else:
        logger.warning("archive_agent: no such agent_id=%s", agent_id)
    return updated


def get_memory(agent_id: str, limit: int = 15) -> List[Dict[str, str]]:
    """
    Returns this persona's own recent-post summaries — the same history the
    writer prompt uses to avoid repeating itself — so the UI can show "what
    the agent remembers" for a given persona.
    """
    return get_recent_post_summaries_for_agent(agent_id, limit=limit)


def get_rejected(agent_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Returns recently rejected candidates (with editorial reason) for one agent,
    so the UI can show why the persona chose not to post about something.
    Sourced from the pipeline event log (evaluate/rejected) — this is the
    "compost heap" view, a filter over pipeline_events, not a separate table.
    """
    return get_rejected_evaluations_for_agent(agent_id, limit=limit)


def get_pipeline_stage_events(agent_id: str, stage: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Returns newest-first pipeline_events for one persona/stage, for the
    dedicated /persona/<id>/<stage> pages.
    """
    if stage not in PIPELINE_STAGES:
        return []
    return get_pipeline_events(agent_id, stage, limit=limit)


def get_pipeline_counts(agent_id: str) -> Dict[str, Any]:
    """
    Returns per-stage counts from the pipeline event log, for the dashboard cards,
    plus today's shared Gemini call-budget usage so the UI can show quota status
    directly instead of making the user dig into stage detail pages to find it.
    """
    counts = get_pipeline_event_counts(agent_id)
    counts["llmCallsUsedToday"] = get_llm_calls_used_today()
    counts["llmDailyCallBudget"] = scheduler_mod._daily_call_budget()
    return counts


def get_feed(agent_id: str) -> dict:
    """
    Returns newest-first posts for the given agent.
    """
    posts = get_posts_for_agent(agent_id)
    ordered = sorted(posts, key=lambda item: item.get("createdAt") or "", reverse=True)
    feed_posts: List[Dict[str, Any]] = []
    for post in ordered:
        feed_posts.append({
            "id": post.get("id"),
            "createdAt": post.get("createdAt"),
            "text": post.get("text"),
            "rationale": post.get("rationale"),
            "sources": post.get("sources", []),
        })
    return {"posts": feed_posts}
