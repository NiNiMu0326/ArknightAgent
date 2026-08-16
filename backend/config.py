"""
Backend LangChain Configuration
"""
import math
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

BASE_DIR = Path(__file__).parent.parent
CHUNKS_DIR = BASE_DIR / "chunks"
GRAPH_DIR = CHUNKS_DIR / "graphrag"
ENTITY_RELATIONS_FILE = GRAPH_DIR / "entity_relations.json"
DATA_DIR = BASE_DIR / "data"


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable (1/true/yes/on)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    """Parse a positive finite float environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"环境变量 {name}={raw!r} 不是有效数字") from None
    if not math.isfinite(value):
        raise ValueError(f"环境变量 {name}={raw!r} 必须是有限数字")
    if value <= 0:
        raise ValueError(f"环境变量 {name}={raw!r} 必须大于 0")
    return value

# PRTS MCP（外部 MCP 工具接入，可选依赖）
PRTS_MCP_ENABLED = _env_bool("PRTS_MCP_ENABLED", True)
PRTS_MCP_COMMAND = os.environ.get("PRTS_MCP_COMMAND", "prts-mcp")
PRTS_MCP_CONNECT_TIMEOUT = _env_float("PRTS_MCP_CONNECT_TIMEOUT", 10.0)
PRTS_MCP_CALL_TIMEOUT = _env_float("PRTS_MCP_CALL_TIMEOUT", 60.0)

# API Keys
SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY_2", "")
SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Model Settings
EMBEDDING_MODEL = "Pro/BAAI/bge-m3"
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
DEEPSEEK_LLM_MODEL = "deepseek-v4-flash"
DEFAULT_TEMPERATURE = 0.7

# Search Settings
RRF_K = 60
VECTOR_WEIGHT = 0.5

# FAISS
FAISS_INDEX_DIR = BASE_DIR / "faiss_index"
FAISS_INDEX_DIR_STR = str(FAISS_INDEX_DIR)


def get_bm25_index_path(collection_name: str) -> str:
    """Get the BM25 index pickle path for a given collection.

    Args:
        collection_name: One of 'operators', 'stories', 'knowledge'.
    """
    return str(CHUNKS_DIR / f"{collection_name}_bm25.pkl")

# LangSmith (activated via environment variables, no code needed):
# Set in .env:
#   LANGCHAIN_TRACING_V2=true
#   LANGCHAIN_API_KEY=your_key
#   LANGCHAIN_PROJECT=arknights-rag

# LangFuse Observability (optional)
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")  # or self-hosted URL
LANGFUSE_ENABLED = bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)
