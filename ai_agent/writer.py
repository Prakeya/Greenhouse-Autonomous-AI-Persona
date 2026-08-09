import json
import logging
import os
import re
from typing import Any, Dict, List

from .memory import get_recent_summaries

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:  # pragma: no cover
    genai = None
    genai_types = None

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or genai is None:
        return None
    _client = genai.Client(api_key=api_key)
    return _client


def _strip_fences(text: str) -> str:
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _parse_json(text: str) -> Dict[str, Any]:
    cleaned = _strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise ValueError("Invalid JSON returned by model")


def _call_llm(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    client = _get_client()
    if client is None:
        logger.error("writer._call_llm: GEMINI_API_KEY missing or google-genai SDK unavailable")
        raise RuntimeError("GEMINI_API_KEY is not configured or google-genai SDK is unavailable")

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0,
            max_output_tokens=768,
            response_mime_type="application/json",
        ),
    )
    text = getattr(response, "text", None) or ""
    return _parse_json(text)


def build_system_prompt(persona: Dict[str, Any]) -> str:
    name = persona.get("name") or "Unnamed agent"
    domain = persona.get("domain") or "general technology"
    voice = persona.get("voice") or "clear, informed, and concise"
    stance = persona.get("stance") or "stays grounded in evidence and technical detail"
    formatting = persona.get("formatting") or "short, readable paragraphs"
    topics = persona.get("topics") or []
    topics_line = f" Specific topics/interests to prioritize: {', '.join(topics)}." if topics else ""
    return (
        f"You are {name}, an autonomous AI persona writing in {domain}.{topics_line} "
        f"Voice: {voice}. Stance: {stance}. Formatting: {formatting}. "
        "Keep the writing consistent with this persona. Write only valid JSON: {'text': 'post body', 'rationale': 'why it matters now', 'sources': ['https://...']}. "
        "Do not use markdown fences. No bullet list markers. Keep the content grounded, specific, and clear."
    )


def generate_post(agent_id: str, candidate: Dict[str, Any], persona: Dict[str, Any]) -> Dict[str, Any]:
    recent = get_recent_summaries(agent_id, n=15)
    history = "\n".join(f"- {item['title']}: {item['summary']}" for item in recent) if recent else "- No recent posts yet."
    system_prompt = build_system_prompt(persona)
    user_prompt = (
        "Write a post in the persona's voice for this topic.\n"
        f"Title: {candidate.get('title', '')}\n"
        f"Summary: {candidate.get('summary', '')}\n"
        f"URL: {candidate.get('url', '')}\n"
        "Recent history:\n"
        f"{history}\n"
        "Return strict JSON with keys text, rationale, sources."
    )

    result = _call_llm(system_prompt, user_prompt)
    text = str(result.get("text") or "")
    rationale = str(result.get("rationale") or "")
    sources = result.get("sources") or [candidate.get("url") or ""]
    if not isinstance(sources, list):
        sources = [str(sources)]
    sources = [str(source).strip() for source in sources if str(source).strip()]
    if not text:
        logger.error("generate_post: model returned empty text for %r", candidate.get("title"))
        raise ValueError("Model returned empty post text")
    logger.debug("generate_post: wrote %d chars for %r", len(text), candidate.get("title"))
    return {"text": text, "rationale": rationale, "sources": sources[:5]}
    # Note: on failure this function now raises rather than returning fabricated
    # content. Callers (ai_agent.scheduler.run_cycle) catch the exception, log it,
    # and skip the candidate instead of publishing fake AI output.
