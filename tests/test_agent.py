import os

from ai_agent.agent import archive_agent, get_feed, get_memory, get_persona, init_agent, list_agents, update_persona


def test_init_agent_returns_id_and_feed_works(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def fake_run_cycle(agent_id):
        from ai_agent.storage import save_post

        save_post(
            {
                "id": "post-1",
                "agent_id": agent_id,
                "title": "Test topic",
                "url": "https://example.com/test",
                "published": "2026-08-08T00:00:00Z",
                "createdAt": "2026-08-08T00:00:00Z",
                "text": "This is a test post.",
                "rationale": "It is relevant.",
                "sources": ["https://example.com/test"],
                "summary": "Test summary",
            }
        )

    import ai_agent.scheduler as scheduler_mod

    monkeypatch.setattr(scheduler_mod, "run_cycle", fake_run_cycle)

    agent_id = init_agent({
        "name": "Ada",
        "domain": "AI Security Researcher",
        "voice": "technical and skeptical",
        "stance": "distrusts benchmark hype",
        "formatting": "short paragraphs",
    })

    feed = get_feed(agent_id)
    assert isinstance(agent_id, str)
    assert len(feed["posts"]) >= 1
    assert feed["posts"][0]["text"] == "This is a test post."


def _make_agent(monkeypatch, **persona_overrides):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    import ai_agent.scheduler as scheduler_mod

    monkeypatch.setattr(scheduler_mod, "run_cycle", lambda agent_id: None)

    persona = {
        "name": "Ada",
        "domain": "AI Security Researcher",
        "voice": "technical and skeptical",
        "stance": "distrusts benchmark hype",
        "formatting": "short paragraphs",
        "topics": ["prompt injection", "model evals"],
    }
    persona.update(persona_overrides)
    return init_agent(persona)


def test_update_persona_partial_field(monkeypatch):
    agent_id = _make_agent(monkeypatch)

    updated = update_persona(agent_id, {"voice": "warmer, more narrative", "topics": ["red teaming"]})

    assert updated["persona"]["voice"] == "warmer, more narrative"
    assert updated["persona"]["topics"] == ["red teaming"]
    # Untouched fields survive a partial update.
    assert updated["persona"]["name"] == "Ada"

    reread = get_persona(agent_id)
    assert reread["persona"]["voice"] == "warmer, more narrative"


def test_update_persona_ignores_unknown_fields(monkeypatch):
    agent_id = _make_agent(monkeypatch)

    updated = update_persona(agent_id, {"not_a_real_field": "nope", "name": "Nova"})

    assert updated["persona"]["name"] == "Nova"
    assert "not_a_real_field" not in updated["persona"]


def test_update_persona_unknown_agent_returns_empty(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert update_persona("does-not-exist", {"name": "X"}) == {}


def test_archive_agent_stops_scheduler_and_hides_from_list(monkeypatch):
    agent_id = _make_agent(monkeypatch)
    assert any(a["id"] == agent_id for a in list_agents())

    result = archive_agent(agent_id)

    assert result is True
    assert not any(a["id"] == agent_id for a in list_agents())
    # Still fetchable directly, just flagged.
    assert get_persona(agent_id)["archived"] is True


def test_archive_unknown_agent_returns_false(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert archive_agent("does-not-exist") is False


def test_get_memory_is_scoped_per_agent(monkeypatch):
    from ai_agent.storage import save_post

    agent_a = _make_agent(monkeypatch)
    agent_b = _make_agent(monkeypatch)

    save_post({
        "id": "post-a", "agent_id": agent_a, "title": "Agent A topic",
        "url": "https://example.com/a", "published": "2026-08-08T00:00:00Z",
        "createdAt": "2026-08-08T00:00:00Z", "text": "A", "rationale": "r",
        "sources": [], "summary": "sum-a",
    })
    save_post({
        "id": "post-b", "agent_id": agent_b, "title": "Agent B topic",
        "url": "https://example.com/b", "published": "2026-08-08T00:00:00Z",
        "createdAt": "2026-08-08T00:00:00Z", "text": "B", "rationale": "r",
        "sources": [], "summary": "sum-b",
    })

    memory_a = get_memory(agent_a)
    titles_a = {m["title"] for m in memory_a}
    assert "Agent A topic" in titles_a
    assert "Agent B topic" not in titles_a
