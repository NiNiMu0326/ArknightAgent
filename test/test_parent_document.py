"""
Tests for backend.rag.parent_document: LRUCache and ParentDocumentRetriever.
Usage: cd test && python -m pytest test_parent_document.py -v
"""
import sys
import time
import threading
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


# ============================================================
# T05: source_file 路径穿越 / 非法输入必须回退 chunk 内容
# ============================================================

@pytest.fixture
def traversal_env(tmp_path):
    """构造「父文档目录 + 目录外机密文件 + 目录 + 非法编码文件」的测试环境。"""
    data_dir = tmp_path / "data"
    operators = data_dir / "operators"
    operators.mkdir(parents=True)
    (operators / "char_002_amiya.md").write_text("阿米娅的完整档案", encoding="utf-8")
    (operators / "subdir").mkdir()                                     # 指向目录的场景
    (operators / "bad_utf8.md").write_bytes(b"\xff\xfe\x80 not utf-8 \x81")
    secret = tmp_path / "secret.md"                                    # 位于 data/ 之外
    secret.write_text("目录外的机密内容", encoding="utf-8")
    retriever = ParentDocumentRetriever(
        chunks_dir=str(tmp_path / "chunks"), data_dir=str(data_dir)
    )
    return retriever, data_dir, secret


# source_file 来自 chunk metadata（可被外部写入），每一种都必须被拒绝并回退。
TRAVERSAL_CASES = {
    "parent_dir_escape": lambda secret: "../secret.md",
    "multi_level_escape": lambda secret: "../../secret.md",
    "absolute_path": lambda secret: str(secret.resolve()),
    "directory_target": lambda secret: "subdir",
    "invalid_utf8_file": lambda secret: "bad_utf8.md",
    "nul_byte_in_name": lambda secret: "char_002\x00.md",
}

FALLBACK = "原始片段"


class TestSourceFileTraversalSafety:
    """防的回归：旧实现直接把 metadata['source_file'] 拼到 data/{source}/ 后 open()，
    于是 '../secret.md'、绝对路径乃至目录都能被读出来（任意文件读取）。
    契约是「解析不到 / 读不出 → 回退 chunk 内容」，且绝不抛异常。"""

    @pytest.mark.parametrize("case", sorted(TRAVERSAL_CASES))
    def test_unsafe_source_file_falls_back_to_chunk_content(self, traversal_env, case):
        retriever, _, secret = traversal_env
        source_file = TRAVERSAL_CASES[case](secret)
        chunk = {"chunk_id": "badid", "content": FALLBACK,
                 "metadata": {"source_file": source_file}}

        result = retriever.get_parent_content(chunk, "operators")

        assert result == FALLBACK
        assert secret.read_text(encoding="utf-8") not in result   # 目录外的内容没被读出来

    def test_retrieve_parent_docs_survives_all_traversal_cases(self, traversal_env):
        """批量入口同样不能因非法路径抛异常，且每条都回退到自己的 chunk 内容。"""
        retriever, _, secret = traversal_env
        chunks = [
            {"chunk_id": "badid", "content": f"{FALLBACK}-{i}",
             "metadata": {"source_file": build(secret)}}
            for i, build in enumerate(TRAVERSAL_CASES.values())
        ]
        results = retriever.retrieve_parent_docs(chunks, "operators")
        assert [r["parent_content"] for r in results] == [
            f"{FALLBACK}-{i}" for i in range(len(chunks))
        ]

    def test_valid_relative_path_still_reads_parent(self, traversal_env):
        """正向对照：合法相对路径必须照常读出父文档，避免上面几条靠「一律拒绝」通过。"""
        retriever, _, _ = traversal_env
        chunk = {"chunk_id": "operators_0001_01", "content": FALLBACK,
                 "metadata": {"source_file": "char_002_amiya.md"}}
        assert retriever.get_parent_content(chunk, "operators") == "阿米娅的完整档案"

    def test_parent_dir_style_path_inside_source_dir_is_still_allowed(self, traversal_env):
        """'../operators/xxx.md' 解析后仍落在 data/operators/ 内：这是合法路径，应当可读。"""
        retriever, _, _ = traversal_env
        chunk = {"chunk_id": "operators_0001_01", "content": FALLBACK,
                 "metadata": {"source_file": "../operators/char_002_amiya.md"}}
        assert retriever.get_parent_content(chunk, "operators") == "阿米娅的完整档案"


# ============================================================
# T32: 并发首次命中只构建一次索引 + 空结果不写负缓存
# ============================================================

class TestSourceIndexConcurrency:
    """防的回归：旧实现「锁内读缓存 → 释放锁 → 扫描 → 再抢锁写回」，
    并发首次命中同一 source 时会重复扫描目录，后写者还可能用较旧的结果覆盖较新的。"""

    def test_concurrent_first_hit_builds_index_once(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        (data_dir / "operators").mkdir(parents=True)
        for i in range(3):
            (data_dir / "operators" / f"op_{i}.md").write_text(f"P{i}", encoding="utf-8")
        retriever = ParentDocumentRetriever(
            chunks_dir=str(tmp_path / "chunks"), data_dir=str(data_dir)
        )

        real_glob = Path.glob
        scans = []

        def counting_glob(self, pattern):
            # 只统计目录扫描；sleep 拉长扫描窗口，保证后续线程都落在锁上等待
            if pattern == "*.md":
                scans.append(str(self))
                time.sleep(0.1)
            return real_glob(self, pattern)

        monkeypatch.setattr(Path, "glob", counting_glob)

        results, errors = [], []
        barrier = threading.Barrier(6, timeout=10)

        def worker():
            try:
                barrier.wait()
                results.append(retriever._build_source_index("operators"))
            except Exception as exc:      # noqa: BLE001 - 线程内异常带回主线程断言
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        assert len(results) == 6
        assert len(scans) == 1, f"并发首次命中重复扫描了目录 {len(scans)} 次"
        assert len({id(r) for r in results}) == 1, "各线程拿到的不是同一个索引对象"
        assert results[0] == {1: "op_0.md", 2: "op_1.md", 3: "op_2.md"}


class TestNoNegativeCaching:
    """防的回归：对「目录不存在 / 目录为空」也写缓存，会让一次暂时性故障
    （挂载未就绪、数据同步未完成）被放大成整个 TTL（1 小时）内的持续失败。"""

    def test_missing_directory_is_not_cached(self, tmp_path):
        data_dir = tmp_path / "data"
        (data_dir / "stories").mkdir(parents=True)     # operators 目录故意不存在
        retriever = ParentDocumentRetriever(
            chunks_dir=str(tmp_path / "chunks"), data_dir=str(data_dir)
        )
        chunk = {"chunk_id": "operators_0001_01", "content": FALLBACK, "metadata": {}}

        assert retriever.get_parent_content(chunk, "operators") == FALLBACK

        # 数据恢复：补上目录与文件后，同一实例必须立刻能取到父文档
        operators = data_dir / "operators"
        operators.mkdir(parents=True)
        (operators / "char_002_amiya.md").write_text("阿米娅的完整档案", encoding="utf-8")

        assert retriever.get_parent_content(chunk, "operators") == "阿米娅的完整档案"

    def test_empty_directory_is_not_cached(self, tmp_path):
        data_dir = tmp_path / "data"
        (data_dir / "operators").mkdir(parents=True)   # 目录存在但没有任何 .md
        retriever = ParentDocumentRetriever(
            chunks_dir=str(tmp_path / "chunks"), data_dir=str(data_dir)
        )

        assert retriever._build_source_index("operators") == {}

        (data_dir / "operators" / "new_op.md").write_text("新干员", encoding="utf-8")
        assert retriever._build_source_index("operators") == {1: "new_op.md"}
