import logging
import warnings
import threading
from typing import Optional
from backend.rag.graphrag.builder import GraphBuilder

logger = logging.getLogger(__name__)

# Module-level singleton for GraphBuilder
_graph_builder_instance: Optional[GraphBuilder] = None
_graph_builder_lock = threading.Lock()

def get_graph_builder() -> Optional[GraphBuilder]:
    """Get or create singleton GraphBuilder instance (thread-safe).

    构建失败时返回 None（而不是未构建成功的 builder），调用方必须判空；
    ``builder.graph`` 在失败时是 build() 提前创建的**空 DiGraph**，把它当
    成有效对象会让关系查询在空图上执行并返回"未找到关系"，掩盖图谱未加载
    的真实故障。

    失败结果不缓存单例，下一次调用会自动重试（例如 entity_relations.json
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
                    logger.warning(
                        "GraphRAG entity_relations.json not found. Relationship queries will be unavailable."
                    )
                    warnings.warn(
                        "GraphRAG entity_relations.json not found. Relationship queries will be unavailable.",
                        stacklevel=2,
                    )
                    return None
                except Exception as e:
                    logger.warning(f"Failed to build GraphRAG knowledge graph: {e}", exc_info=True)
                    warnings.warn(f"Failed to build GraphRAG knowledge graph: {e}", stacklevel=2)
                    return None
                _graph_builder_instance = builder
    return _graph_builder_instance
