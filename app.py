import os

from dotenv import load_dotenv

load_dotenv()

from ai_agent.logging_config import configure_logging

configure_logging()

import logging

from flask import Flask, jsonify, request, send_from_directory

from ai_agent.agent import (
    PIPELINE_STAGES,
    archive_agent,
    get_feed,
    get_memory,
    get_persona,
    get_pipeline_counts,
    get_pipeline_stage_events,
    get_rejected,
    init_agent,
    list_agents,
    resume_all_agents,
    update_persona,
)

logger = logging.getLogger(__name__)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")


@app.post("/api/agent/init")
def init_route():
    payload = request.get_json(silent=True) or {}
    persona = payload.get("persona") or {}
    if not isinstance(persona, dict):
        persona = {}
    raw_topics = persona.get("topics")
    topics = [str(t).strip() for t in raw_topics if str(t).strip()] if isinstance(raw_topics, list) else []
    persona_config = {
        "name": str(persona.get("name") or "Ada"),
        "domain": str(persona.get("domain") or "AI Research"),
        "voice": str(persona.get("voice") or "clear, skeptical, technical"),
        "stance": str(persona.get("stance") or "prefers evidence over hype"),
        "formatting": str(persona.get("formatting") or "short readable paragraphs"),
        "topics": topics,
    }
    agent_id = init_agent(persona_config)
    logger.info(
        "Persona created: id=%s name=%s domain=%s topics=%s",
        agent_id, persona_config["name"], persona_config["domain"], persona_config["topics"],
    )
    return jsonify({"agentId": agent_id}), 201


@app.get("/api/agent/feed")
def feed_route():
    agent_id = request.args.get("agentId")
    if not agent_id:
        return jsonify({"error": {"code": "MISSING_AGENT_ID", "message": "agentId query param is required"}}), 422
    return jsonify(get_feed(agent_id))


@app.get("/api/agent/persona")
def persona_route():
    agent_id = request.args.get("agentId")
    if not agent_id:
        return jsonify({"error": {"code": "MISSING_AGENT_ID", "message": "agentId query param is required"}}), 422
    persona = get_persona(agent_id)
    if not persona:
        return jsonify({"error": {"code": "AGENT_NOT_FOUND", "message": "No agent found for that id"}}), 404
    return jsonify(persona)


@app.patch("/api/agent/persona")
def update_persona_route():
    agent_id = request.args.get("agentId")
    if not agent_id:
        return jsonify({"error": {"code": "MISSING_AGENT_ID", "message": "agentId query param is required"}}), 422
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not payload:
        return jsonify({"error": {"code": "EMPTY_BODY", "message": "Request body must be a JSON object with fields to update"}}), 422
    existing = get_persona(agent_id)
    if not existing:
        return jsonify({"error": {"code": "AGENT_NOT_FOUND", "message": "No agent found for that id"}}), 404
    updated = update_persona(agent_id, payload)
    logger.info("Persona updated: id=%s fields=%s", agent_id, sorted(payload.keys()))
    return jsonify(updated)


@app.delete("/api/agent/persona")
def archive_persona_route():
    agent_id = request.args.get("agentId")
    if not agent_id:
        return jsonify({"error": {"code": "MISSING_AGENT_ID", "message": "agentId query param is required"}}), 422
    existing = get_persona(agent_id)
    if not existing:
        return jsonify({"error": {"code": "AGENT_NOT_FOUND", "message": "No agent found for that id"}}), 404
    archive_agent(agent_id)
    logger.info("Persona archived: id=%s", agent_id)
    return jsonify({"id": agent_id, "archived": True}), 200


@app.get("/api/agent/memory")
def memory_route():
    agent_id = request.args.get("agentId")
    if not agent_id:
        return jsonify({"error": {"code": "MISSING_AGENT_ID", "message": "agentId query param is required"}}), 422
    existing = get_persona(agent_id)
    if not existing:
        return jsonify({"error": {"code": "AGENT_NOT_FOUND", "message": "No agent found for that id"}}), 404
    limit = request.args.get("limit", default=15, type=int) or 15
    summaries = get_memory(agent_id, limit=limit)
    return jsonify({"memory": summaries})


@app.get("/api/agent/list")
def list_agents_route():
    include_archived = request.args.get("includeArchived", "false").lower() in {"1", "true", "yes"}
    return jsonify({"agents": list_agents(include_archived=include_archived)})


@app.get("/api/agent/rejected")
def rejected_route():
    agent_id = request.args.get("agentId")
    if not agent_id:
        return jsonify({"error": {"code": "MISSING_AGENT_ID", "message": "agentId query param is required"}}), 422
    return jsonify({"rejected": get_rejected(agent_id)})


@app.get("/api/agent/pipeline/counts")
def pipeline_counts_route():
    agent_id = request.args.get("agentId")
    if not agent_id:
        return jsonify({"error": {"code": "MISSING_AGENT_ID", "message": "agentId query param is required"}}), 422
    return jsonify(get_pipeline_counts(agent_id))


@app.get("/api/agent/pipeline/<stage>")
def pipeline_stage_route(stage):
    agent_id = request.args.get("agentId")
    if not agent_id:
        return jsonify({"error": {"code": "MISSING_AGENT_ID", "message": "agentId query param is required"}}), 422
    if stage not in PIPELINE_STAGES:
        return jsonify({"error": {"code": "UNKNOWN_STAGE", "message": f"stage must be one of {sorted(PIPELINE_STAGES)}"}}), 404
    limit = request.args.get("limit", default=100, type=int) or 100
    events = get_pipeline_stage_events(agent_id, stage, limit=limit)
    return jsonify({"stage": stage, "events": events})


@app.get("/api/health")
def health_route():
    return jsonify({"status": "ok", "geminiConfigured": bool(os.getenv("GEMINI_API_KEY"))})


@app.get("/")
def index_route():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/persona/<agent_id>")
@app.get("/persona/<agent_id>/<stage>")
def persona_page_route(agent_id, stage=None):
    # Client-rendered SPA: these paths just need to serve the same shell as "/".
    # app.js reads location.pathname on boot and renders the dashboard or one of
    # the four dedicated stage pages (discover/evaluate/write/publish) accordingly.
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.errorhandler(404)
def not_found(_err):
    # Let unmatched non-API routes fall back to the SPA shell; real 404s for /api/* already
    # return structured JSON from their own handlers above.
    if request.path.startswith("/api/"):
        logger.warning("404 on API route: %s", request.path)
        return jsonify({"error": {"code": "NOT_FOUND", "message": "Route not found"}}), 404
    return send_from_directory(FRONTEND_DIR, "index.html")


# Re-attach schedulers for any personas already saved in the database. The scheduler
# registry lives in memory, so a process restart would otherwise leave existing
# personas persisted but silently inactive.
resumed_count = resume_all_agents()
logger.info("Startup: resumed %d existing persona(s)", resumed_count)


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting server on %s:%d", host, port)
    app.run(host=host, port=port, debug=False)
