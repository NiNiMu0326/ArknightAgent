"""
Tests for backend.rag.parent_document: LRUCache and ParentDocumentRetriever.
Usage: cd test && python -m pytest test_parent_document.py -v
"""
import sys
import time
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.rag.parent_document import LRUCache, ParentDocumentRetriever


class TestLRUCache:
    def test_set_and_get(self):
        c = LRUCache(max_size=5)
        c.set("a", "value_a")
        assert c.get("a") == "value_a"

    def test_get_missing(self):
        c = LRUCache(max_size=5)
        assert c.get("missing") is None

    def test_contains(self):
        c = LRUCache(max_size=5)
        c.set("a", "v")
        assert "a" in c
        assert "b" not in c

    def test_eviction_when_full(self):
        c = LRUCache(max_size=3)
        c.set("a", "1")
        c.set("b", "2")
        c.set("c", "3")
        # "a" is least recently used
        c.set("d", "4")
        assert c.get("a") is None
        assert c.get("b") == "2"
        assert c.get("c") == "3"
        assert c.get("d") == "4"

    def test_mru_move_on_access(self):
        c = LRUCache(max_size=3)
        c.set("a", "1")
        c.set("b", "2")
        c.set("c", "3")
        # Access "a" to make it most recently used
        c.get("a")
        # Now "b" is LRU
        c.set("d", "4")
        assert c.get("b") is None
        assert c.get("a") == "1"

    def test_overwrite_existing(self):
        c = LRUCache(max_size=5)
        c.set("a", "old")
        c.set("a", "new")
        assert c.get("a") == "new"
        assert len(c) == 1

    def test_len(self):
        c = LRUCache(max_size=10)
        assert len(c) == 0
        c.set("a", "1")
        c.set("b", "2")
        assert len(c) == 2

    def test_ttl_expiry_get(self):
        c = LRUCache(max_size=10, ttl_seconds=0.01)
        c.set("a", "v")
        time.sleep(0.02)
        assert c.get("a") is None

    def test_ttl_not_expired(self):
        c = LRUCache(max_size=10, ttl_seconds=5)
        c.set("a", "v")
        assert c.get("a") == "v"

    def test_ttl_expiry_contains(self):
        c = LRUCache(max_size=10, ttl_seconds=0.01)
        c.set("a", "v")
        time.sleep(0.02)
        assert "a" not in c


# ============================================================
# ParentDocumentRetriever
# ============================================================

@pytest.fixture
def pdr_env(tmp_path):
    """Create a retriever with a temp data dir containing two operator files."""
    data_dir = tmp_path / "data"
    (data_dir / "operators").mkdir(parents=True)
    (data_dir / "stories").mkdir(parents=True)
    (data_dir / "operators" / "char_002_amiya.md").write_text("阿米娅的完整档案", encoding="utf-8")
    (data_dir / "operators" / "char_003_silverash.md").write_text("银灰的完整档案", encoding="utf-8")
    (data_dir / "stories" / "story_001.md").write_text("故事全文内容", encoding="utf-8")
    retriever = ParentDocumentRetriever(
        chunks_dir=str(tmp_path / "chunks"), data_dir=str(data_dir)
    )
    return retriever, data_dir


class TestBuildSourceIndex:
    def test_index_maps_one_based_positions(self, pdr_env):
        retriever, _ = pdr_env
        index = retriever._build_source_index("operators", "_operators_index_cache", "_operators_index_timestamp")
        assert index == {1: "char_002_amiya.md", 2: "char_003_silverash.md"}

    def test_missing_directory_returns_empty(self, pdr_env):
        retriever, _ = pdr_env
        index = retriever._build_source_index("nonexistent", "_operators_index_cache", "_operators_index_timestamp")
        assert index == {}

    def test_index_cached_within_ttl(self, pdr_env):
        retriever, data_dir = pdr_env
        first = retriever._build_source_index("operators", "_operators_index_cache", "_operators_index_timestamp")
        # Add a file — cached index should NOT see it
        (data_dir / "operators" / "char_999_new.md").write_text("新干员", encoding="utf-8")
        second = retriever._build_source_index("operators", "_operators_index_cache", "_operators_index_timestamp")
        assert first is second
        assert len(second) == 2


class TestGetParentFile:
    def test_resolves_operator_chunk_id(self, pdr_env):
        retriever, _ = pdr_env
        assert retriever._get_parent_file("operators_0001_01", "operators") == "char_002_amiya.md"
        assert retriever._get_parent_file("operators_0002_03", "operators") == "char_003_silverash.md"

    def test_resolves_story_chunk_id(self, pdr_env):
        retriever, _ = pdr_env
        assert retriever._get_parent_file("stories_0001_01", "stories") == "story_001.md"

    def test_out_of_range_index_returns_none(self, pdr_env):
        retriever, _ = pdr_env
        assert retriever._get_parent_file("operators_9999_01", "operators") is None

    def test_malformed_chunk_id_returns_none(self, pdr_env):
        retriever, _ = pdr_env
        assert retriever._get_parent_file("badid", "operators") is None
        assert retriever._get_parent_file("operators_abc_01", "operators") is None

    def test_unknown_source_returns_none(self, pdr_env):
        retriever, _ = pdr_env
        assert retriever._get_parent_file("knowledge_0001", "knowledge") is None


class TestGetParentContent:
    def test_reads_file_from_metadata_source_file(self, pdr_env):
        retriever, _ = pdr_env
        chunk = {"chunk_id": "operators_0001_01", "content": "片段",
                 "metadata": {"source_file": "char_002_amiya.md"}}
        assert retriever.get_parent_content(chunk, "operators") == "阿米娅的完整档案"

    def test_derives_file_from_chunk_id_when_no_metadata(self, pdr_env):
        retriever, _ = pdr_env
        chunk = {"chunk_id": "operators_0002_01", "content": "片段", "metadata": {}}
        assert retriever.get_parent_content(chunk, "operators") == "银灰的完整档案"

    def test_falls_back_to_chunk_content_when_unresolvable(self, pdr_env):
        retriever, _ = pdr_env
        chunk = {"chunk_id": "badid", "content": "原始片段", "metadata": {}}
        assert retriever.get_parent_content(chunk, "operators") == "原始片段"

    def test_faiss_chunk_filename_falls_back_to_chunk_id(self, pdr_env):
        """FAISS 元数据里的 source_file 是 chunk 文件名，应回退用 chunk_id 找父文档。"""
        retriever, _ = pdr_env
        chunk = {"chunk_id": "operators_0002_01", "content": "片段",
                 "metadata": {"source_file": "operators_0002_01.md"}}
        assert retriever.get_parent_content(chunk, "operators") == "银灰的完整档案"

    def test_falls_back_when_file_missing_on_disk(self, pdr_env):
        retriever, _ = pdr_env
        chunk = {"chunk_id": "operators_0001_01", "content": "原始片段",
                 "metadata": {"source_file": "ghost.md"}}
        assert retriever.get_parent_content(chunk, "operators") == "原始片段"

    def test_unknown_source_returns_chunk_content(self, pdr_env):
        retriever, _ = pdr_env
        chunk = {"chunk_id": "knowledge_1", "content": "知识片段",
                 "metadata": {"source_file": "x.md"}}
        assert retriever.get_parent_content(chunk, "knowledge") == "知识片段"

    def test_content_cached_after_first_read(self, pdr_env):
        retriever, data_dir = pdr_env
        chunk = {"chunk_id": "operators_0001_01", "content": "片段", "metadata": {}}
        first = retriever.get_parent_content(chunk, "operators")
        # Modify file on disk — second read must come from cache
        (data_dir / "operators" / "char_002_amiya.md").write_text("已被修改", encoding="utf-8")
        second = retriever.get_parent_content(chunk, "operators")
        assert first == second == "阿米娅的完整档案"


class TestRetrieveParentDocs:
    def test_batch_expansion(self, pdr_env):
        retriever, _ = pdr_env
        chunks = [
            {"chunk_id": "operators_0001_01", "content": "c1", "metadata": {"section": "基础档案"}, "score": 0.9},
            {"chunk_id": "operators_0002_01", "content": "c2", "metadata": {}, "score": 0.8},
        ]
        results = retriever.retrieve_parent_docs(chunks, "operators")
        assert len(results) == 2
        assert results[0]["parent_content"] == "阿米娅的完整档案"
        assert results[0]["section"] == "基础档案"
        assert results[0]["score"] == 0.9
        assert results[0]["source"] == "operators"
        assert results[1]["parent_content"] == "银灰的完整档案"

    def test_empty_input(self, pdr_env):
        retriever, _ = pdr_env
        assert retriever.retrieve_parent_docs([], "operators") == []
