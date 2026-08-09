import os
import tempfile

import pytest

from ai_agent import storage


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Point ai_agent.storage at a throwaway SQLite file for every test.

    Without this, tests call the real init_agent()/storage functions
    against ai_agent/agent_store.sqlite3 — the same file the live app
    reads from — so running `pytest` pollutes the running app's persona
    list with test data (duplicate "Ada" entries, a stray "Nova" from the
    rename test, etc.). Each test now gets its own empty database.
    """
    db_path = os.path.join(tmp_path, "test_agent_store.sqlite3")
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    storage.init_db()
    yield
