import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)

HACKER_NEWS_URL = "https://hn.algolia.com/api/v1/search_by_date?query=AI&tags=story"
ARXIV_URL = "http://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending"


def _parse_datetime(value: str) -> datetime:
    value = (value or "").strip()
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S+00:00", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _clean_text(value: str) -> str:
    if value is None:
        return ""
    return " ".join(value.replace("\n", " ").split())


def fetch_hn_candidates() -> List[Dict[str, object]]:
    try:
        response = requests.get(HACKER_NEWS_URL, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("fetch_hn_candidates: request failed: %s", exc)
        return []

    items = payload.get("hits") or []
    results: List[Dict[str, object]] = []
    for item in items[:10]:
        title = _clean_text(item.get("title") or item.get("story_title") or "")
        url = item.get("url") or item.get("story_url") or ""
        summary = _clean_text(item.get("story_text") or item.get("title") or "")
        published = _parse_datetime(item.get("created_at") or item.get("created_at_i") or "")
        if title:
            results.append({"title": title, "summary": summary[:280], "url": url, "published": published})
    logger.info("fetch_hn_candidates: %d candidate(s)", len(results))
    return results


def fetch_arxiv_candidates() -> List[Dict[str, object]]:
    try:
        response = requests.get(ARXIV_URL, timeout=20)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception as exc:
        logger.warning("fetch_arxiv_candidates: request failed: %s", exc)
        return []

    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)
    results: List[Dict[str, object]] = []
    for entry in entries[:10]:
        title = _clean_text(entry.findtext("a:title", default="", namespaces=ns))
        summary = _clean_text(entry.findtext("a:summary", default="", namespaces=ns))
        link = entry.find("a:id", ns)
        published = entry.findtext("a:published", default="", namespaces=ns)

        if not title:
            continue
        url = link.text if link is not None else ""
        results.append({
            "title": title,
            "summary": summary[:280],
            "url": url,
            "published": _parse_datetime(published),
        })
    logger.info("fetch_arxiv_candidates: %d candidate(s)", len(results))
    return results


def discover_candidates() -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []
    for candidate_list in (fetch_hn_candidates(), fetch_arxiv_candidates()):
        for candidate in candidate_list:
            if not candidate.get("title"):
                continue
            # Per-agent dedup happens in scheduler.run_cycle via memory.is_duplicate(agent_id, candidate).
            # discover_candidates() has no agent context, so it must not dedup here.
            results.append(candidate)
    deduped: List[Dict[str, object]] = []
    seen = set()
    for candidate in results:
        key = (candidate.get("title") or "").strip().lower(), (candidate.get("url") or "").strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    logger.info("discover_candidates: %d unique candidate(s) after dedup", len(deduped))
    return deduped
