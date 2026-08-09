import logging
import re
from difflib import SequenceMatcher
from typing import Dict, List

from .storage import get_recent_posts_for_agent, get_recent_post_summaries_for_agent

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def is_duplicate(agent_id: str, candidate: Dict[str, str]) -> bool:
    """Checks near-duplicates against THIS agent's own recent posts only.
    Previously checked against all agents' posts globally, which meant one
    persona's coverage could silently suppress another persona's coverage of
    the same topic. Fixed as part of Phase 3 (agent-scoped memory)."""
    recent_posts = get_recent_posts_for_agent(agent_id, limit=25)
    candidate_title = _normalize(candidate.get("title") or "")
    candidate_url = (candidate.get("url") or "").strip().lower()

    for post in recent_posts:
        post_title = _normalize(post.get("title") or "")
        post_url = (post.get("url") or "").strip().lower()

        if candidate_url and post_url and candidate_url == post_url:
            return True
        if candidate_title and post_title:
            score = SequenceMatcher(None, candidate_title, post_title).ratio()
            if score >= 0.78:
                logger.debug(
                    "is_duplicate: agent_id=%s candidate %r matched recent post %r (score=%.2f)",
                    agent_id, candidate.get("title"), post.get("title"), score,
                )
                return True
    return False


def get_recent_summaries(agent_id: str, n: int = 15) -> List[Dict[str, str]]:
    return get_recent_post_summaries_for_agent(agent_id, limit=n)
