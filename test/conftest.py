"""Pytest configuration for all tests."""
import os
import sys
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set required env vars for testing (before any imports)
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-tests")
os.environ["PRTS_MCP_ENABLED"] = "false"


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    """Initialize SQLite tables for tests that hit the real app.

    ASGITransport does not trigger FastAPI startup events, and CI runs on a
    fresh checkout without data/arknights_rag.db, so tables must be created
    explicitly.
    """
    import asyncio

    from backend.db import init_db

    asyncio.run(init_db())
