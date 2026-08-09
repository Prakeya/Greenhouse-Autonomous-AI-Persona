import json
import logging
import os
import re
from typing import Any, Dict

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:  # pragma: no cover
    genai = None
    genai_types = None

from .llm_errors import RateLimitedError, is_rate_limit_error

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
        raise RuntimeError("GEMINI_API_KEY is not configured or google-genai SDK is unavailable")

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0,
            max_output_tokens=512,
            response_mime_type="application/json",
        ),
    )
    text = getattr(response, "text", None) or ""
    return _parse_json(text)


def build_persona_system_prompt(persona: Dict[str, Any]) -> str:
    name = persona.get("name") or "Unnamed agent"
    domain = persona.get("domain") or "general technology"
    voice = persona.get("voice") or "clear, informed, and concise"
    stance = persona.get("stance") or "stays grounded in evidence and technical detail"
    formatting = persona.get("formatting") or "short, readable paragraphs"
    topics = persona.get("topics") or []
    topics_line = f" Specific topics/interests to prioritize: {', '.join(topics)}." if topics else ""
    return (
        f"You are {name}, an autonomous editorial persona in {domain}.{topics_line} "
        f"Voice: {voice}. Stance: {stance}. Formatting: {formatting}. "
        "You must produce strict JSON only. Do not wrap in markdown fences. "
        "Return a JSON object with exactly these keys: {'verdict': 'accept' | 'reject', 'reason': '1-2 sentence explanation'}. "
        "Reject pure marketing copy, product launch announcements with no technical substance, vague claims without evidence, and topics already covered in memory. "
        "Accept only technically relevant, timely, and arguable topics that match the persona's domain and editorial stance."
    )


def judge_candidate(agent_id: str, candidate: Dict[str, Any], persona: Dict[str, Any]) -> Dict[str, Any]:
    system_prompt = build_persona_system_prompt(persona)
    user_prompt = (
        "Review this candidate as an editor with the persona above.\n"
        f"Title: {candidate.get('title', '')}\n"
        f"Summary: {candidate.get('summary', '')}\n"
        f"URL: {candidate.get('url', '')}\n"
        "Return JSON only with verdict and reason."
    )

    try:
        result = _call_llm(system_prompt, user_prompt)
        verdict = str(result.get("verdict", "reject")).lower()
        reason = str(result.get("reason") or "Rejected by editorial review.")
    except Exception as exc:
        logger.exception("judge_candidate: LLM call failed for %r: %s", candidate.get("title"), exc)
        if is_rate_limit_error(exc):
            raise RateLimitedError(str(exc)) from exc
        verdict = "reject"
        reason = f"Editorial review could not run: {type(exc).__name__}: {exc}"

    if verdict not in {"accept", "reject"}:
        verdict = "reject"

    # Rejections are now logged by the caller (scheduler.run_cycle) as pipeline_events,
    # which is the single source of truth for evaluate-stage outcomes going forward.
    return {"verdict": verdict, "reason": reason}
