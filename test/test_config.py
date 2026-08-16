"""
Tests for backend.config: paths, API keys, model settings.
Usage: cd test && python -m pytest test_config.py -v
"""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import config


# ============================================================
# Path configuration
# ============================================================

class TestConfigPaths:
    """Test path constants are correctly set."""

    def test_base_dir_exists(self):
        assert config.BASE_DIR.exists()
        assert config.BASE_DIR.is_dir()

    def test_base_dir_is_project_root(self):
        """BASE_DIR should point to the project root (one level above backend/)."""
        assert (config.BASE_DIR / "backend").exists()
        assert (config.BASE_DIR / "data").exists()

    def test_chunks_dir(self):
        assert config.CHUNKS_DIR == config.BASE_DIR / "chunks"

    def test_graph_dir(self):
        assert config.GRAPH_DIR == config.CHUNKS_DIR / "graphrag"

    def test_entity_relations_file(self):
        assert config.ENTITY_RELATIONS_FILE == config.GRAPH_DIR / "entity_relations.json"

    def test_data_dir(self):
        assert config.DATA_DIR == config.BASE_DIR / "data"

    def test_faiss_index_dir(self):
        assert config.FAISS_INDEX_DIR == config.BASE_DIR / "faiss_index"
        assert config.FAISS_INDEX_DIR_STR == str(config.FAISS_INDEX_DIR)


# ============================================================
# API configuration
# ============================================================

class TestConfigAPI:
    """Test API-related configuration values."""

    def test_siliconflow_base_url(self):
        assert config.SILICONFLOW_BASE_URL == "https://api.siliconflow.cn/v1"

    def test_deepseek_base_url(self):
        assert config.DEEPSEEK_BASE_URL == "https://api.deepseek.com"

    def test_api_keys_are_strings(self):
        assert isinstance(config.SILICONFLOW_API_KEY, str)
        assert isinstance(config.TAVILY_API_KEY, str)
        assert isinstance(config.DEEPSEEK_API_KEY, str)


# ============================================================
# Model settings
# ============================================================

class TestConfigModels:
    """Test model-related configuration."""

    def test_embedding_model(self):
        assert config.EMBEDDING_MODEL == "Pro/BAAI/bge-m3"

    def test_reranker_model(self):
        assert config.RERANKER_MODEL == "BAAI/bge-reranker-v2-m3"

    def test_deepseek_llm_model(self):
        assert config.DEEPSEEK_LLM_MODEL == "deepseek-v4-flash"

    def test_default_temperature(self):
        assert 0 <= config.DEFAULT_TEMPERATURE <= 2.0


# ============================================================
# Search settings
# ============================================================

class TestConfigSearch:
    """Test search-related configuration."""

    def test_rrf_k_is_positive(self):
        assert config.RRF_K > 0

    def test_vector_weight_is_valid(self):
        assert 0.0 <= config.VECTOR_WEIGHT <= 1.0


# ============================================================
# get_bm25_index_path
# ============================================================

class TestGetBm25IndexPath:
    """Test BM25 index path helper function."""

    def test_returns_string(self):
        path = config.get_bm25_index_path("operators")
        assert isinstance(path, str)

    def test_ends_with_collection_name(self):
        path = config.get_bm25_index_path("operators")
        assert path.endswith("operators_bm25.pkl")

    def test_starts_with_chunks_dir(self):
        path = config.get_bm25_index_path("stories")
        assert path.startswith(str(config.CHUNKS_DIR))

    def test_different_collections_different_paths(self):
        p1 = config.get_bm25_index_path("operators")
        p2 = config.get_bm25_index_path("stories")
        assert p1 != p2


# ============================================================
# JWT_SECRET
# ============================================================

class TestJWTSecret:
    """JWT_SECRET should be set (from conftest.py)."""

    def test_jwt_secret_is_set(self):
        """conftest.py sets JWT_SECRET for tests."""
        import os
        assert os.environ.get("JWT_SECRET") == "test-jwt-secret-for-tests"


# ============================================================
# _env_bool
# ============================================================

class TestEnvBool:
    """_env_bool 环境变量布尔解析。"""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("true", True),
            ("TRUE", True),
            ("1", True),
            ("yes", True),
            ("ON", True),
            ("false", False),
            ("0", False),
            ("no", False),
            ("  true  ", True),
        ],
    )
    def test_parse_values(self, monkeypatch, raw, expected):
        monkeypatch.setenv("TEST_ENV_BOOL", raw)
        assert config._env_bool("TEST_ENV_BOOL", not expected) is expected

    def test_missing_env_uses_default_true(self, monkeypatch):
        monkeypatch.delenv("TEST_ENV_BOOL", raising=False)
        assert config._env_bool("TEST_ENV_BOOL", True) is True

    def test_missing_env_uses_default_false(self, monkeypatch):
        monkeypatch.delenv("TEST_ENV_BOOL", raising=False)
        assert config._env_bool("TEST_ENV_BOOL", False) is False

    def test_unknown_value_is_false(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_BOOL", "tru")
        assert config._env_bool("TEST_ENV_BOOL", True) is False


# ============================================================
# PRTS MCP 配置
# ============================================================

class TestPrtsMcpConfig:
    """PRTS MCP 配置项（测试环境由 conftest 关闭）。"""

    def _probe_default(self, pop_keys, expr):
        repo_root = Path(__file__).resolve().parents[1]
        clean_env = os.environ.copy()
        for key in pop_keys:
            clean_env.pop(key, None)
        code = (
            "import dotenv; dotenv.load_dotenv=lambda *a, **k: None; "
            f"import backend.config as c; print({expr})"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo_root,
            env={**clean_env, "PYTHONPATH": str(repo_root)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, f"subprocess failed:\n{proc.stderr}"
        return proc.stdout.strip()

    def test_prts_mcp_disabled_in_tests(self):
        assert config.PRTS_MCP_ENABLED is False

    def test_prts_mcp_command_default(self):
        assert self._probe_default(["PRTS_MCP_COMMAND"], "c.PRTS_MCP_COMMAND") == "prts-mcp"

    def test_prts_mcp_connect_timeout_default(self):
        assert self._probe_default(["PRTS_MCP_CONNECT_TIMEOUT"], "c.PRTS_MCP_CONNECT_TIMEOUT") == "10.0"

    def test_prts_mcp_call_timeout_default(self):
        assert self._probe_default(["PRTS_MCP_CALL_TIMEOUT"], "c.PRTS_MCP_CALL_TIMEOUT") == "60.0"

    def test_default_enabled_true_when_env_absent(self):
        assert self._probe_default(["PRTS_MCP_ENABLED"], "c.PRTS_MCP_ENABLED") == "True"

    def test_invalid_timeout_raises(self, monkeypatch):
        monkeypatch.setenv("PRTS_MCP_CONNECT_TIMEOUT", "abc")
        try:
            with pytest.raises(ValueError):
                importlib.reload(config)
        finally:
            monkeypatch.delenv("PRTS_MCP_CONNECT_TIMEOUT", raising=False)
            importlib.reload(config)

    def test_negative_timeout_raises(self, monkeypatch):
        monkeypatch.setenv("PRTS_MCP_CONNECT_TIMEOUT", "-1")
        try:
            with pytest.raises(ValueError):
                importlib.reload(config)
        finally:
            monkeypatch.delenv("PRTS_MCP_CONNECT_TIMEOUT", raising=False)
            importlib.reload(config)


class TestEnvFloatValidation:
    @pytest.mark.parametrize("bad_value", ["nan", "inf", "0"])
    def test_non_finite_or_non_positive_timeout_raises(self, bad_value, monkeypatch):
        monkeypatch.setenv("PRTS_MCP_CONNECT_TIMEOUT", bad_value)
        try:
            with pytest.raises(ValueError):
                importlib.reload(config)
        finally:
            monkeypatch.delenv("PRTS_MCP_CONNECT_TIMEOUT", raising=False)
            importlib.reload(config)
