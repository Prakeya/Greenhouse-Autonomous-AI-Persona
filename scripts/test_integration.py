import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List

import requests

PORT = int(os.getenv("PORT", "8000"))
BASE = os.getenv("BASE_URL", f"http://localhost:{PORT}")


def assert_iso8601(value: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:  # pragma: no cover
        raise AssertionError(f"Invalid ISO8601 value: {value!r}") from exc


def check_feed(agent_id: str) -> List[Dict[str, Any]]:
    response = requests.get(f"{BASE}/api/agent/feed", params={"agentId": agent_id}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    posts = payload.get("posts", [])
    if not isinstance(posts, list):
        raise AssertionError(f"Feed payload must contain a list: {payload!r}")
    return posts


def main() -> None:
    init_payload = {"persona": {"name": "Ada", "domain": "AI Security Researcher"}}
    init_response = requests.post(f"{BASE}/api/agent/init", json=init_payload, timeout=30)
    init_response.raise_for_status()
    agent_id = init_response.json().get("agentId")
    if not agent_id:
        raise AssertionError(f"No agentId returned: {init_response.text}")

    seen_ids = set()
    previous_count = 0
    last_seen = []
    for _ in range(6):
        time.sleep(30)
        posts = check_feed(agent_id)
        if posts:
            for post in posts:
                post_id = post.get("id")
                if not post_id:
                    raise AssertionError(f"Missing post id: {post!r}")
                assert post_id not in seen_ids, f"Duplicate id seen: {post_id}"
                seen_ids.add(post_id)
                assert isinstance(post.get("text"), str) and post["text"].strip()
                assert isinstance(post.get("rationale"), str) and post["rationale"].strip()
                assert isinstance(post.get("sources"), list) and post["sources"]
                assert_iso8601(post["createdAt"])
            assert posts == sorted(posts, key=lambda p: p["createdAt"], reverse=True), "Posts are not reverse-chronological"
            if previous_count and len(posts) < previous_count:
                raise AssertionError(f"Feed shrank from {previous_count} to {len(posts)}")
            previous_count = len(posts)
            last_seen = posts

    print(f"agentId={agent_id}")
    print(f"total_posts={len(seen_ids)}")
    print("feed_snapshot=")
    for post in last_seen[:5]:
        print(post)
    print("INTEGRATION_CHECK=PASS")


if __name__ == "__main__":
    main()
