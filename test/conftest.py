"""Pytest configuration for all tests."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set required env vars for testing (before any imports)
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-tests")
os.environ["PRTS_MCP_ENABLED"] = "false"

# 测试库隔离：必须在导入 backend.db 之前把 DB_PATH 指向临时文件。
# 否则 init_db() 与各接口测试会往真实的 data/arknights_rag.db 里注册测试账号
# （历史上已累积数百个 test###/dup### 账号），污染本地开发数据。
import backend.db as _db  # noqa: E402

_DB_TMP_DIR = Path(tempfile.mkdtemp(prefix="arknights-pytest-"))
_db.DB_PATH = _DB_TMP_DIR / "arknights_rag.db"  # 刻意不改源码：测试期覆盖模块级路径


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    """Initialize SQLite tables for tests that hit the real app.

    ASGITransport does not trigger FastAPI startup events, and CI runs on a
    fresh checkout without data/arknights_rag.db, so tables must be created
    explicitly in the isolated temp database configured above.
    """
    import asyncio

    from backend.db import init_db

    asyncio.run(init_db())
