import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from apscheduler.schedulers.background import BackgroundScheduler

from .discovery import discover_candidates
from .judge import judge_candidate
from .llm_errors import RateLimitedError, is_rate_limit_error
from .memory import is_duplicate
from .storage import (
    get_llm_calls_used_today,
    increment_llm_calls_used,
    log_pipeline_event,
    mark_llm_budget_exhausted_today,
    save_post,
)
from .writer import generate_post

logger = logging.getLogger(__name__)

_AGENTS: Dict[str, Dict[str, Any]] = {}
_SCHEDULERS: Dict[str, BackgroundScheduler] = {}
_LOCK = threading.Lock()


def _safe_log_event(
    agent_id: str,
    cycle_id: str,
    stage: str,
    status: str,
    candidate: Dict[str, Any] = None,
    content: str = None,
    reason: str = None,
    metadata: Dict[str, Any] = None,
) -> None:
    """log_pipeline_event wrapped so a durability failure can never break the
    calling pipeline stage or fake a status the stage didn't actually reach."""
    candidate = candidate or {}
    try:
        log_pipeline_event(
            persona_id=agent_id,
            cycle_id=cycle_id,
            stage=stage,
            status=status,
            source_url=candidate.get("url"),
            title=candidate.get("title") or "Untitled",
            snippet=candidate.get("summary"),
            content=content,
            reason=reason,
            metadata=metadata,
        )
    except Exception:  # pragma: no cover - never let logging break the cycle
        logger.exception(
            "_safe_log_event: failed to persist pipeline event agent=%s cycle=%s stage=%s status=%s",
            agent_id, cycle_id, stage, status,
        )


def _build_post_record(agent_id: str, candidate: Dict[str, Any], content: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "title": candidate.get("title") or "Untitled",
        "summary": candidate.get("summary") or "",
        "url": candidate.get("url") or "",
        "published": (candidate.get("published") or datetime.now(timezone.utc)).isoformat(),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "text": content["text"],
        "rationale": content["rationale"],
        "sources": content["sources"],
    }


def _daily_call_budget() -> int:
    """Total Gemini generate_content calls (judge + write combined, across all
    personas sharing this key) to allow per UTC day, kept a little under
    Gemini's free-tier cap of 20/day so we stop ourselves before Google does.
    Raise this (e.g. to a few hundred) once billing is enabled on the key."""
    try:
        value = int(os.getenv("GEMINI_DAILY_CALL_BUDGET") or "18")
        return max(1, value)
    except ValueError:
        return 18


def _max_candidates_per_cycle() -> int:
    """How many candidates to spend judge-LLM calls on per cycle. Defaults low
    (5) because Gemini's free tier is capped at 20 generate_content calls/day
    total across judge + write — evaluating all 10 discovered candidates every
    cycle exhausts that in one or two runs. Override with
    AGENT_MAX_CANDIDATES_PER_CYCLE if you're on a paid tier with more headroom."""
    try:
        value = int(os.getenv("AGENT_MAX_CANDIDATES_PER_CYCLE") or "5")
        return max(1, min(value, 25))
    except ValueError:
        return 5


def run_cycle(agent_id: str) -> None:
    with _LOCK:
        agent = _AGENTS.get(agent_id)
    if not agent:
        logger.warning("run_cycle called for unknown agent_id=%s (not in registry)", agent_id)
        return

    persona = agent.get("persona") or {}
    cycle_id = str(uuid.uuid4())
    logger.info("Cycle start: agent=%s cycle=%s persona=%s", agent_id, cycle_id, persona.get("name"))

    try:
        candidates = discover_candidates()
    except Exception as exc:  # pragma: no cover - external API resilience
        logger.exception("discover failed for agent=%s cycle=%s: %s", agent_id, cycle_id, exc)
        _safe_log_event(agent_id, cycle_id, "discover", "failed", reason=str(exc))
        return

    logger.info("Cycle agent=%s cycle=%s: discovered %d candidate(s)", agent_id, cycle_id, len(candidates))
    for candidate in candidates:
        _safe_log_event(agent_id, cycle_id, "discover", "found", candidate=candidate)

    try:
        accepted: List[Dict[str, Any]] = []
        rate_limited = False
        budget = _daily_call_budget()
        calls_used = get_llm_calls_used_today()
        if calls_used >= budget:
            logger.info(
                "Cycle agent=%s: daily Gemini call budget already spent (%d/%d) — skipping evaluate/write this cycle",
                agent_id, calls_used, budget,
            )
            _safe_log_event(
                agent_id, cycle_id, "evaluate", "skipped",
                reason=f"Daily Gemini call budget used up ({calls_used}/{budget}); skipping until tomorrow (UTC) or until the budget is raised.",
            )
            rate_limited = True

        for candidate in candidates[: _max_candidates_per_cycle()]:
            if rate_limited:
                break
            try:
                if is_duplicate(agent_id, candidate):
                    logger.debug("Cycle agent=%s: skipping duplicate %r", agent_id, candidate.get("title"))
                    _safe_log_event(
                        agent_id, cycle_id, "evaluate", "rejected", candidate=candidate,
                        reason="Duplicate of an existing post",
                    )
                    continue
                if get_llm_calls_used_today() >= budget:
                    _safe_log_event(
                        agent_id, cycle_id, "evaluate", "skipped", candidate=candidate,
                        reason=f"Daily Gemini call budget ({budget}) reached mid-cycle; stopping evaluate here.",
                    )
                    rate_limited = True
                    break
                judgment = judge_candidate(agent_id, candidate, persona)
                increment_llm_calls_used(1)
                verdict = judgment.get("verdict")
                reason = judgment.get("reason")
                logger.info(
                    "Cycle agent=%s: judged %r -> %s (%s)",
                    agent_id, candidate.get("title"), verdict, reason,
                )
                _safe_log_event(
                    agent_id, cycle_id, "evaluate",
                    "accepted" if verdict == "accept" else "rejected",
                    candidate=candidate, reason=reason, metadata={"verdict": verdict},
                )
                if verdict == "accept":
                    accepted.append(candidate)
                    if len(accepted) >= 2:
                        break
            except RateLimitedError as exc:
                # Quota exhaustion says nothing about this candidate (or any of the
                # remaining ones) — stop spending calls this cycle instead of
                # mislabeling every remaining candidate as editorially rejected,
                # and mark the whole day's budget spent so nobody else retries a
                # dead quota with a real request either.
                logger.warning("Cycle agent=%s: Gemini quota/rate limit hit, stopping evaluate stage early: %s", agent_id, exc)
                mark_llm_budget_exhausted_today()
                _safe_log_event(
                    agent_id, cycle_id, "evaluate", "skipped", candidate=candidate,
                    reason=f"Gemini quota/rate limit reached; will retry next cycle. Detail: {exc}",
                )
                rate_limited = True
                break
            except Exception as exc:  # pragma: no cover - external API resilience
                logger.exception("judge failed for candidate %s: %s", candidate.get("title"), exc)
                _safe_log_event(
                    agent_id, cycle_id, "evaluate", "failed", candidate=candidate,
                    reason=f"Judge stage failed: {exc}",
                )
                continue

        for candidate in accepted:
            if rate_limited:
                break
            if get_llm_calls_used_today() >= budget:
                _safe_log_event(
                    agent_id, cycle_id, "write", "skipped", candidate=candidate,
                    reason=f"Daily Gemini call budget ({budget}) reached before this draft could be written.",
                )
                break
            try:
                content = generate_post(agent_id, candidate, persona)
                increment_llm_calls_used(1)
            except Exception as exc:  # pragma: no cover - generation resilience
                logger.exception("write failed for %s: %s", candidate.get("title"), exc)
                if is_rate_limit_error(exc):
                    logger.warning("Cycle agent=%s: Gemini quota/rate limit hit during write, stopping early: %s", agent_id, exc)
                    mark_llm_budget_exhausted_today()
                    _safe_log_event(
                        agent_id, cycle_id, "write", "skipped", candidate=candidate,
                        reason=f"Gemini quota/rate limit reached; will retry next cycle. Detail: {exc}",
                    )
                    rate_limited = True
                else:
                    _safe_log_event(
                        agent_id, cycle_id, "write", "failed", candidate=candidate,
                        reason=f"Content generation failed: {exc}",
                    )
                continue

            # Log the draft immediately after successful generation, before the
            # publish attempt, so this event survives even if save_post fails below.
            _safe_log_event(
                agent_id, cycle_id, "write", "drafted", candidate=candidate,
                content=content["text"],
                metadata={"rationale": content.get("rationale"), "sources": content.get("sources")},
            )

            post = _build_post_record(agent_id, candidate, content)
            try:
                save_post(post)
                logger.info("Cycle agent=%s: published post id=%s title=%r", agent_id, post["id"], candidate.get("title"))
                _safe_log_event(
                    agent_id, cycle_id, "publish", "published", candidate=candidate,
                    content=content["text"],
                    metadata={"postId": post["id"], "rationale": content.get("rationale"), "sources": content.get("sources")},
                )
            except Exception as exc:  # pragma: no cover - storage resilience
                logger.exception("publish failed for %s: %s", candidate.get("title"), exc)
                _safe_log_event(
                    agent_id, cycle_id, "publish", "failed", candidate=candidate,
                    reason=f"Publish failed: {exc}",
                )
        logger.info("Cycle end: agent=%s cycle=%s accepted=%d", agent_id, cycle_id, len(accepted))
    except Exception as exc:  # pragma: no cover - top-level cycle resilience
        logger.exception("agent cycle failed for %s cycle=%s: %s", agent_id, cycle_id, exc)


def start_scheduler(agent_id: str, interval_minutes: int = 20) -> None:
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: run_cycle(agent_id), "interval", minutes=max(1, int(interval_minutes)), id=f"agent_{agent_id}")
    scheduler.start()
    _SCHEDULERS[agent_id] = scheduler
    logger.info("Scheduler started: agent=%s interval=%dmin", agent_id, interval_minutes)


def stop_scheduler(agent_id: str) -> None:
    scheduler = _SCHEDULERS.get(agent_id)
    if scheduler:
        scheduler.shutdown()
        _SCHEDULERS.pop(agent_id, None)
        logger.info("Scheduler stopped: agent=%s", agent_id)


def get_agent_registry(agent_id: str) -> Dict[str, Any]:
    return _AGENTS.get(agent_id, {})


def set_agent_registry(agent_id: str, persona: Dict[str, Any]) -> None:
    _AGENTS[agent_id] = {"persona": persona}
