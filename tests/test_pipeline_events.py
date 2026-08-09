import uuid

from ai_agent.agent import (
    get_pipeline_counts,
    get_pipeline_stage_events,
    get_rejected,
    init_agent,
)
from ai_agent.storage import get_pipeline_events, log_pipeline_event


def _make_agent(monkeypatch, **overrides):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    import ai_agent.scheduler as scheduler_mod

    monkeypatch.setattr(scheduler_mod, "run_cycle", lambda agent_id: None)
    persona = {
        "name": "Ada",
        "domain": "AI Security Researcher",
        "voice": "technical and skeptical",
        "stance": "distrusts benchmark hype",
        "formatting": "short paragraphs",
        "topics": ["prompt injection"],
    }
    persona.update(overrides)
    return init_agent(persona)


# ---------- storage-layer basics ----------

def test_log_and_read_pipeline_event(monkeypatch):
    agent_id = _make_agent(monkeypatch)
    cycle_id = str(uuid.uuid4())

    log_pipeline_event(
        persona_id=agent_id, cycle_id=cycle_id, stage="discover", status="found",
        source_url="https://example.com/x", title="A story", snippet="summary",
    )

    events = get_pipeline_events(agent_id, "discover")
    assert len(events) == 1
    assert events[0]["stage"] == "discover"
    assert events[0]["status"] == "found"
    assert events[0]["cycleId"] == cycle_id
    assert events[0]["title"] == "A story"


def test_pipeline_events_scoped_per_agent(monkeypatch):
    agent_a = _make_agent(monkeypatch)
    agent_b = _make_agent(monkeypatch)
    cycle = str(uuid.uuid4())

    log_pipeline_event(persona_id=agent_a, cycle_id=cycle, stage="discover", status="found", title="A")
    log_pipeline_event(persona_id=agent_b, cycle_id=cycle, stage="discover", status="found", title="B")

    events_a = get_pipeline_stage_events(agent_a, "discover")
    titles_a = {e["title"] for e in events_a}
    assert "A" in titles_a
    assert "B" not in titles_a


def test_unknown_stage_returns_empty(monkeypatch):
    agent_id = _make_agent(monkeypatch)
    assert get_pipeline_stage_events(agent_id, "not-a-real-stage") == []


# ---------- full cycle, exercising the real run_cycle instrumentation ----------

def test_full_cycle_writes_events_for_every_stage(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    import ai_agent.scheduler as scheduler_mod

    from datetime import datetime, timezone
    published = datetime(2026, 8, 8, tzinfo=timezone.utc)
    candidates = [
        {"title": "Accepted story", "summary": "sum-1", "url": "https://example.com/1", "published": published},
        {"title": "Rejected story", "summary": "sum-2", "url": "https://example.com/2", "published": published},
    ]

    monkeypatch.setattr(scheduler_mod, "discover_candidates", lambda: candidates)
    monkeypatch.setattr(scheduler_mod, "is_duplicate", lambda agent_id, candidate: False)

    def fake_judge(agent_id, candidate, persona):
        if candidate["title"] == "Accepted story":
            return {"verdict": "accept", "reason": "On-topic and technical."}
        return {"verdict": "reject", "reason": "Too promotional."}

    monkeypatch.setattr(scheduler_mod, "judge_candidate", fake_judge)

    def fake_generate(agent_id, candidate, persona):
        return {"text": "Draft body text.", "rationale": "Matters now.", "sources": [candidate["url"]]}

    monkeypatch.setattr(scheduler_mod, "generate_post", fake_generate)

    # init_agent's own first-run cycle uses the real (unmocked) run_cycle here,
    # so this exercises the actual instrumented pipeline end to end.
    agent_id = init_agent({
        "name": "Ada", "domain": "AI Security", "voice": "technical",
        "stance": "skeptical", "formatting": "short",
    })

    discover_events = get_pipeline_stage_events(agent_id, "discover")
    evaluate_events = get_pipeline_stage_events(agent_id, "evaluate")
    write_events = get_pipeline_stage_events(agent_id, "write")
    publish_events = get_pipeline_stage_events(agent_id, "publish")

    # Discover: one event per discovered candidate.
    assert len(discover_events) == 2
    assert {e["status"] for e in discover_events} == {"found"}

    # Evaluate: one accepted, one rejected, both with reasoning stored.
    assert len(evaluate_events) == 2
    statuses = {e["title"]: e["status"] for e in evaluate_events}
    assert statuses["Accepted story"] == "accepted"
    assert statuses["Rejected story"] == "rejected"
    rejected_event = next(e for e in evaluate_events if e["status"] == "rejected")
    assert rejected_event["reason"] == "Too promotional."

    # Write: drafted event exists with the actual draft content.
    assert len(write_events) == 1
    assert write_events[0]["status"] == "drafted"
    assert write_events[0]["content"] == "Draft body text."
    assert write_events[0]["title"] == "Accepted story"

    # Publish: published event exists with final content.
    assert len(publish_events) == 1
    assert publish_events[0]["status"] == "published"
    assert publish_events[0]["content"] == "Draft body text."

    # All events from this run share one cycle_id.
    cycle_ids = {e["cycleId"] for e in discover_events + evaluate_events + write_events + publish_events}
    assert len(cycle_ids) == 1

    # Compost heap: rejected candidates surface through the pipeline-events-backed view.
    rejected = get_rejected(agent_id)
    assert any(r["title"] == "Rejected story" and r["reason"] == "Too promotional." for r in rejected)

    # Dashboard counts reflect the event log.
    counts = get_pipeline_counts(agent_id)
    assert counts["discover"] == 2
    assert counts["evaluate"]["accepted"] == 1
    assert counts["evaluate"]["rejected"] == 1
    assert counts["write"] == 1
    assert counts["publish"] == 1
    assert counts["latestCycleId"] in cycle_ids


def test_write_failure_still_leaves_evaluate_events_and_no_publish(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    import ai_agent.scheduler as scheduler_mod

    from datetime import datetime, timezone
    candidates = [{"title": "Bad draft", "summary": "s", "url": "https://example.com/z", "published": datetime(2026, 8, 8, tzinfo=timezone.utc)}]
    monkeypatch.setattr(scheduler_mod, "discover_candidates", lambda: candidates)
    monkeypatch.setattr(scheduler_mod, "is_duplicate", lambda agent_id, candidate: False)
    monkeypatch.setattr(scheduler_mod, "judge_candidate", lambda agent_id, candidate, persona: {"verdict": "accept", "reason": "ok"})

    def failing_generate(agent_id, candidate, persona):
        raise ValueError("model returned empty text")

    monkeypatch.setattr(scheduler_mod, "generate_post", failing_generate)

    agent_id = init_agent({
        "name": "Ada", "domain": "AI Security", "voice": "technical",
        "stance": "skeptical", "formatting": "short",
    })

    write_events = get_pipeline_stage_events(agent_id, "write")
    publish_events = get_pipeline_stage_events(agent_id, "publish")
    assert len(write_events) == 1
    assert write_events[0]["status"] == "failed"
    assert "model returned empty text" in write_events[0]["reason"]
    assert len(publish_events) == 0
