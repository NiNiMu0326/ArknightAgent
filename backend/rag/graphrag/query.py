import warnings
import threading
from typing import Optional
from backend.rag.graphrag.builder import GraphBuilder

# Module-level singleton for GraphBuilder
_graph_builder_instance: Optional[GraphBuilder] = None
_graph_builder_lock = threading.Lock()

def get_graph_builder() -> GraphBuilder:
    """Get or create singleton GraphBuilder instance (thread-safe).

    构建失败时不缓存单例，下一次调用会自动重试（例如 entity_relations.json
    在服务启动后才生成的情况）。
    """
    global _graph_builder_instance
    if _graph_builder_instance is None:
        with _graph_builder_lock:
            # Double-check locking pattern
            if _graph_builder_instance is None:
                builder = GraphBuilder()
                try:
                    builder.build()
                except FileNotFoundError:
                    _graph_builder_instance = None  # 下次重试
                    warnings.warn("GraphRAG entity_relations.json not found. Relationship queries will be unavailable.")
                except Exception as e:
                    _graph_builder_instance = None  # 下次重试
                    warnings.warn(f"Failed to build GraphRAG knowledge graph: {e}")
                else:
                    _graph_builder_instance = builder
                return _graph_builder_instance or builder
    return _graph_builder_instance


