"""
Tests for backend.agent.tool_implementations:
execute_rag_search / execute_graphrag_search / execute_web_search,
web-search dedup state, and lazy BM25 index loading.
Usage: cd test && python -m pytest test_tool_implementations.py -v
"""
import asyncio
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.agent import tool_implementations as ti
from backend.agent.tool_result import ToolResultPayload


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset module-level singletons between tests."""
    ti._bm25_indexes = None
    ti._web_search_seen.clear()
    yield
    ti._bm25_indexes = None
    ti._web_search_seen.clear()


def make_doc(content, chunk_id, score=0.9, collection="operators"):
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {
        "chunk_id": chunk_id,
        "source_collection": collection,
        "relevance_score": score,
    }
    return doc


# ============================================================
# execute_rag_search
# ============================================================

class TestExecuteRagSearch:
    def test_empty_query_returns_error(self):
        result = run(ti.execute_rag_search({}))
        assert result == [{"error": "query parameter is required"}]

    def _run_rag(self, args, docs, parent_content=None):
        with patch.object(ti, "_get_bm25_indexes", return_value={}), \
             patch("backend.lc.embeddings.SiliconFlowEmbeddings"), \
             patch("backend.rag.retrievers.MultiChannelRetriever") as MockRetriever, \
             patch("backend.lc.reranker.SiliconFlowReranker") as MockReranker, \
             patch("backend.rag.parent_document.ParentDocumentRetriever") as MockParent:
            MockRetriever.return_value.invoke.return_value = docs
            MockReranker.return_value.compress_documents.return_value = docs
            MockParent.return_value.get_parent_content.return_value = parent_content
            result = run(ti.execute_rag_search(args))
            return result, MockRetriever, MockReranker

    def test_basic_results_shape(self):
        docs = [make_doc("银灰的技能是...", "operators_0001_01", score=0.91234)]
        result, _, _ = self._run_rag({"query": "银灰", "top_k": 1}, docs)
        assert len(result) == 1
        item = result[0]
        assert item["source"] == "operators"
        assert item["chunk_id"] == "operators_0001_01"
        assert item["score"] == 0.9123  # rounded to 4 decimals
        assert "银灰" in item["content"]

    def test_parent_expansion_for_operators_chunk(self):
        """operators_ chunks should be expanded to parent content when longer."""
        docs = [make_doc("短chunk", "operators_0001_01")]
        long_parent = "完整的干员档案内容" * 50
        result, _, _ = self._run_rag(
            {"query": "银灰", "enable_parent_expansion": True}, docs,
            parent_content=long_parent,
        )
        assert result[0]["content"] == long_parent[:2000]

    def test_parent_expansion_disabled(self):
        docs = [make_doc("原始chunk内容", "operators_0001_01")]
        long_parent = "完整内容" * 100
        result, _, _ = self._run_rag(
            {"query": "银灰", "enable_parent_expansion": False}, docs,
            parent_content=long_parent,
        )
        assert result[0]["content"] == "原始chunk内容"

    def test_no_expansion_for_knowledge_chunks(self):
        """knowledge_ chunks are not eligible for parent expansion."""
        docs = [make_doc("知识chunk", "knowledge_0001", collection="knowledge")]
        result, _, _ = self._run_rag({"query": "q"}, docs, parent_content="x" * 5000)
        assert result[0]["content"] == "知识chunk"

    def test_dedup_after_parent_expansion(self):
        """Chunks expanding to identical parent content should be deduplicated."""
        docs = [
            make_doc("chunk1", "operators_0001_01", score=0.9),
            make_doc("chunk2", "operators_0001_02", score=0.8),
        ]
        same_parent = "相同的父文档内容" * 50
        result, _, _ = self._run_rag({"query": "q"}, docs, parent_content=same_parent)
        assert len(result) == 1  # second identical expansion dropped

    def test_search_mode_vector_weight_mapping(self):
        """search_mode should map to the expected vector_weight for RRF fusion."""
        docs = [make_doc("c", "knowledge_1")]
        for mode, expected in [("precise", 0.25), ("semantic", 0.75), ("balanced", 0.5)]:
            _, MockRetriever, _ = self._run_rag({"query": "q", "search_mode": mode}, docs)
            kwargs = MockRetriever.call_args
            assert kwargs.kwargs["vector_weight"] == expected or kwargs[1]["vector_weight"] == expected

    def test_unknown_search_mode_defaults_to_balanced(self):
        docs = [make_doc("c", "knowledge_1")]
        _, MockRetriever, _ = self._run_rag({"query": "q", "search_mode": "bogus"}, docs)
        kwargs = MockRetriever.call_args
        assert kwargs.kwargs["vector_weight"] == 0.5 or kwargs[1]["vector_weight"] == 0.5

    def test_exception_returns_error_dict(self):
        with patch.object(ti, "_get_bm25_indexes", side_effect=RuntimeError("boom")):
            result = run(ti.execute_rag_search({"query": "银灰"}))
            assert len(result) == 1
            assert "error" in result[0]
            assert "检索失败" in result[0]["error"]

    def test_content_truncated_at_2000_chars(self):
        docs = [make_doc("x" * 5000, "knowledge_1", collection="knowledge")]
        result, _, _ = self._run_rag({"query": "q"}, docs)
        assert len(result[0]["content"]) == 2000


# ============================================================
# execute_graphrag_search
# ============================================================

class TestExecuteGraphragSearch:
    def test_graph_not_loaded(self):
        with patch("backend.rag.graphrag.query.get_graph_builder", return_value=None):
            result = run(ti.execute_graphrag_search({"entity": "银灰"}))
            assert "error" in result
            assert "知识图谱未加载" in result["error"]

    def test_graph_none_attribute(self):
        builder = MagicMock()
        builder.graph = None
        with patch("backend.rag.graphrag.query.get_graph_builder", return_value=builder):
            result = run(ti.execute_graphrag_search({"entity": "银灰"}))
            assert "error" in result

    def test_no_params_returns_error(self):
        builder = MagicMock()
        with patch("backend.rag.graphrag.query.get_graph_builder", return_value=builder):
            result = run(ti.execute_graphrag_search({}))
            assert result == {"error": "请提供 entity 或 entity1+entity2 参数"}

    def test_single_entity_neighbors_found(self):
        builder = MagicMock()
        builder.get_neighbors.return_value = [{"entity": "恩希欧迪斯"}]
        builder.get_all_relations.return_value = [{"relation": "兄妹"}]
        with patch("backend.rag.graphrag.query.get_graph_builder", return_value=builder):
            result = run(ti.execute_graphrag_search({"entity": "银灰"}))
            assert isinstance(result, ToolResultPayload)
            llm = result.llm_content
            assert llm["found"] is True
            assert llm["mode"] == "neighbors"
            assert llm["entity"] == "银灰"
            assert llm["neighbors"] == [{"entity": "恩希欧迪斯"}]
            # LLM 通道不重复携带 relations；前端展示保留完整数据
            assert "relations" not in llm
            assert result.display["relations"] == [{"relation": "兄妹"}]

    def test_single_entity_not_found(self):
        builder = MagicMock()
        builder.get_neighbors.return_value = []
        builder.get_all_relations.return_value = []
        with patch("backend.rag.graphrag.query.get_graph_builder", return_value=builder):
            result = run(ti.execute_graphrag_search({"entity": "不存在的人"}))
            assert result["found"] is False
            assert "不存在的人" in result["message"]

    def test_two_entity_path_found(self):
        builder = MagicMock()
        builder.find_path.return_value = {
            "path": ["银灰", "初雪"],
            "edges": [{"from": "银灰", "to": "初雪", "relation": "兄妹"}],
        }
        with patch("backend.rag.graphrag.query.get_graph_builder", return_value=builder):
            result = run(ti.execute_graphrag_search({"entity1": "银灰", "entity2": "初雪"}))
            assert result["found"] is True
            assert result["mode"] == "path"
            assert result["path"] == ["银灰", "初雪"]
            builder.find_path.assert_called_once_with("银灰", "初雪", max_hops=3)

    def test_two_entity_path_not_found(self):
        builder = MagicMock()
        builder.find_path.return_value = {"path": None, "edges": []}
        with patch("backend.rag.graphrag.query.get_graph_builder", return_value=builder):
            result = run(ti.execute_graphrag_search({"entity1": "A", "entity2": "B"}))
            assert result["found"] is False
            assert "A" in result["message"] and "B" in result["message"]

    def test_exception_returns_error(self):
        with patch("backend.rag.graphrag.query.get_graph_builder", side_effect=RuntimeError("db down")):
            result = run(ti.execute_graphrag_search({"entity": "银灰"}))
            assert "error" in result
            assert "关系查询失败" in result["error"]


# ============================================================
# execute_web_search
# ============================================================

class TestExecuteWebSearch:
    def test_empty_query_returns_error(self):
        result = run(ti.execute_web_search({}))
        assert result == [{"error": "query parameter is required"}]

    def test_results_shape(self):
        with patch("backend.api.web_search.search") as mock_search:
            mock_search.return_value = [
                {"title": "银灰wiki", "url": "http://example.com/1", "snippet": "银灰是..."},
            ]
            result = run(ti.execute_web_search({"query": "银灰"}, session_id="s1"))
            assert len(result) == 1
            item = result[0]
            assert item["title"] == "银灰wiki"
            assert item["url"] == "http://example.com/1"
            assert item["source_id"] == "web"

    def test_no_results_returns_message(self):
        with patch("backend.api.web_search.search", return_value=[]):
            result = run(ti.execute_web_search({"query": "q"}, session_id="s1"))
            assert "未找到相关网络搜索结果" in result[0]["message"]

    def test_session_url_dedup(self):
        """Same URL returned twice in one session should be deduplicated."""
        with patch("backend.api.web_search.search") as mock_search:
            mock_search.return_value = [
                {"title": "t", "url": "http://example.com/dup", "snippet": "s"},
            ]
            first = run(ti.execute_web_search({"query": "q"}, session_id="s1"))
            assert len(first) == 1
            second = run(ti.execute_web_search({"query": "q"}, session_id="s1"))
            assert "已在之前返回" in second[0]["message"]

    def test_different_sessions_not_deduped(self):
        with patch("backend.api.web_search.search") as mock_search:
            mock_search.return_value = [
                {"title": "t", "url": "http://example.com/x", "snippet": "s"},
            ]
            run(ti.execute_web_search({"query": "q"}, session_id="s1"))
            result = run(ti.execute_web_search({"query": "q"}, session_id="s2"))
            assert len(result) == 1
            assert result[0]["url"] == "http://example.com/x"

    def test_dedup_fallback_to_content_when_no_url(self):
        with patch("backend.api.web_search.search") as mock_search:
            mock_search.return_value = [{"title": "t", "url": "", "snippet": "相同内容片段"}]
            first = run(ti.execute_web_search({"query": "q"}, session_id="s1"))
            assert len(first) == 1
            second = run(ti.execute_web_search({"query": "q"}, session_id="s1"))
            assert "已在之前返回" in second[0]["message"]

    def test_exception_returns_error(self):
        with patch("backend.api.web_search.search", side_effect=RuntimeError("network")):
            result = run(ti.execute_web_search({"query": "q"}, session_id="s1"))
            assert "error" in result[0]
            assert "网络搜索失败" in result[0]["error"]


# ============================================================
# Web search dedup state management
# ============================================================

class TestWebSearchDedupState:
    def test_clear_web_search_seen(self):
        ti._web_search_seen["s1"] = {"http://x"}
        ti.clear_web_search_seen("s1")
        assert "s1" not in ti._web_search_seen

    def test_clear_nonexistent_session_noop(self):
        ti.clear_web_search_seen("does-not-exist")  # should not raise

    def test_cleanup_removes_oldest_20_percent(self):
        for i in range(ti._web_search_seen_max_size + 1):
            ti._web_search_seen[f"session-{i}"] = {f"http://x/{i}"}
        ti._cleanup_web_search_seen()
        expected_remaining = (ti._web_search_seen_max_size + 1) - (ti._web_search_seen_max_size + 1) // 5
        assert len(ti._web_search_seen) == expected_remaining

    def test_cleanup_noop_below_threshold(self):
        ti._web_search_seen["only-one"] = {"http://x"}
        ti._cleanup_web_search_seen()
        assert len(ti._web_search_seen) == 1


# ============================================================
# _get_bm25_indexes lazy loading
# ============================================================

class TestGetBm25Indexes:
    def test_loads_all_collections(self):
        with patch("backend.config.get_bm25_index_path", side_effect=lambda name: f"/fake/{name}.pkl"), \
             patch("backend.data.bm25_index.BM25Indexer") as MockBM25:
            MockBM25.load.side_effect = lambda path: f"index:{path}"
            indexes = ti._get_bm25_indexes()
            assert set(indexes.keys()) == {"operators", "stories", "knowledge"}
            assert indexes["operators"] == "index:/fake/operators.pkl"

    def test_cached_on_second_call(self):
        with patch("backend.config.get_bm25_index_path", side_effect=lambda name: f"/fake/{name}.pkl"), \
             patch("backend.data.bm25_index.BM25Indexer") as MockBM25:
            MockBM25.load.side_effect = lambda path: f"index:{path}"
            first = ti._get_bm25_indexes()
            second = ti._get_bm25_indexes()
            assert first is second
            assert MockBM25.load.call_count == 3  # loaded only once

    def test_missing_index_file_is_tolerated(self):
        with patch("backend.config.get_bm25_index_path", side_effect=lambda name: f"/fake/{name}.pkl"), \
             patch("backend.data.bm25_index.BM25Indexer") as MockBM25:
            MockBM25.load.side_effect = FileNotFoundError("no such file")
            indexes = ti._get_bm25_indexes()
            assert indexes == {}

    def test_partial_failure_keeps_available_indexes(self):
        def load_side_effect(path):
            if "stories" in path:
                raise FileNotFoundError("missing")
            return f"index:{path}"
        with patch("backend.config.get_bm25_index_path", side_effect=lambda name: f"/fake/{name}.pkl"), \
             patch("backend.data.bm25_index.BM25Indexer") as MockBM25:
            MockBM25.load.side_effect = load_side_effect
            indexes = ti._get_bm25_indexes()
            assert "stories" not in indexes
            assert "operators" in indexes
            assert "knowledge" in indexes

    def test_unexpected_load_error_is_tolerated(self):
        with patch("backend.config.get_bm25_index_path", side_effect=lambda name: f"/fake/{name}.pkl"), \
             patch("backend.data.bm25_index.BM25Indexer") as MockBM25:
            MockBM25.load.side_effect = RuntimeError("corrupted pickle")
            indexes = ti._get_bm25_indexes()
            assert indexes == {}
