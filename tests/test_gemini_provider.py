import pytest

from ai_agent import judge, writer


PERSONA = {
    "name": "Ada",
    "domain": "AI Security Researcher",
    "voice": "technical and skeptical",
    "stance": "distrusts benchmark hype",
    "formatting": "short paragraphs",
    "topics": ["prompt injection"],
}

CANDIDATE = {
    "title": "New attack found in RAG pipelines",
    "summary": "Researchers describe a prompt injection technique.",
    "url": "https://example.com/rag-attack",
}


# ---------- health endpoint reports Gemini configuration ----------

def test_health_endpoint_reports_gemini_configured_true(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    import app as app_module

    client = app_module.app.test_client()
    response = client.get("/api/health")
    body = response.get_json()
    assert body["geminiConfigured"] is True
    assert "anthropicConfigured" not in body


def test_health_endpoint_reports_gemini_configured_false(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import app as app_module

    client = app_module.app.test_client()
    response = client.get("/api/health")
    body = response.get_json()
    assert body["geminiConfigured"] is False


# ---------- missing GEMINI_API_KEY fails safely ----------

def test_judge_candidate_missing_gemini_key_rejects_safely(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = judge.judge_candidate("agent-1", CANDIDATE, PERSONA)

    assert result["verdict"] == "reject"
    assert result["reason"]


def test_generate_post_missing_gemini_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(RuntimeError):
        writer._call_llm("system prompt", "user prompt")


# ---------- malformed / invalid Gemini response fails safely ----------

def test_judge_candidate_malformed_response_rejects_safely(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(judge, "_call_llm", lambda system_prompt, user_prompt: (_ for _ in ()).throw(ValueError("Invalid JSON returned by model")))

    result = judge.judge_candidate("agent-1", CANDIDATE, PERSONA)

    assert result["verdict"] == "reject"
    assert "ValueError" in result["reason"]


def test_judge_candidate_quota_error_raises_rate_limited_instead_of_rejecting(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        judge,
        "_call_llm",
        lambda system_prompt, user_prompt: (_ for _ in ()).throw(
            RuntimeError("429 RESOURCE_EXHAUSTED: You exceeded your current quota")
        ),
    )

    with pytest.raises(judge.RateLimitedError):
        judge.judge_candidate("agent-1", CANDIDATE, PERSONA)


def test_generate_post_malformed_response_does_not_publish_fake_content(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(writer, "_call_llm", lambda system_prompt, user_prompt: (_ for _ in ()).throw(ValueError("Invalid JSON returned by model")))
    monkeypatch.setattr(writer, "get_recent_summaries", lambda agent_id, n=15: [])

    with pytest.raises(ValueError):
        writer.generate_post("agent-1", CANDIDATE, PERSONA)


def test_generate_post_empty_text_raises_instead_of_publishing(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(writer, "_call_llm", lambda system_prompt, user_prompt: {"text": "", "rationale": "r", "sources": []})
    monkeypatch.setattr(writer, "get_recent_summaries", lambda agent_id, n=15: [])

    with pytest.raises(ValueError):
        writer.generate_post("agent-1", CANDIDATE, PERSONA)


# ---------- judge output parsing ----------

def test_judge_candidate_parses_accept_verdict(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        judge,
        "_call_llm",
        lambda system_prompt, user_prompt: {"verdict": "ACCEPT", "reason": "Technically substantive and on-topic."},
    )

    result = judge.judge_candidate("agent-1", CANDIDATE, PERSONA)

    assert result["verdict"] == "accept"
    assert result["reason"] == "Technically substantive and on-topic."


def test_judge_candidate_normalizes_unexpected_verdict_to_reject(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        judge,
        "_call_llm",
        lambda system_prompt, user_prompt: {"verdict": "maybe", "reason": "Unclear."},
    )

    result = judge.judge_candidate("agent-1", CANDIDATE, PERSONA)

    assert result["verdict"] == "reject"


# ---------- writer success path still returns expected shape ----------

def test_generate_post_returns_expected_shape_on_success(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        writer,
        "_call_llm",
        lambda system_prompt, user_prompt: {
            "text": "A grounded, specific post body.",
            "rationale": "It matters now because of X.",
            "sources": [CANDIDATE["url"]],
        },
    )
    monkeypatch.setattr(writer, "get_recent_summaries", lambda agent_id, n=15: [])

    result = writer.generate_post("agent-1", CANDIDATE, PERSONA)

    assert result["text"] == "A grounded, specific post body."
    assert result["rationale"] == "It matters now because of X."
    assert result["sources"] == [CANDIDATE["url"]]
