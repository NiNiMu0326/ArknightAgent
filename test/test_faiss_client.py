"""
Tests for backend.storage.faiss_client: FAISSClientWrapper.
Usage: cd test && python -m pytest test_faiss_client.py -v
"""
import sys
import json
import pickle
import tempfile
import numpy as np
import pytest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.storage.faiss_client import FAISSClientWrapper


# ============================================================
# Helper: create a minimal LangChain-like Document
# ============================================================

class FakeDocument:
    """Minimal LangChain Document stand-in for testing."""
    def __init__(self, page_content, metadata=None):
        self.page_content = page_content
        self.metadata = metadata or {}


# ============================================================
# Dummy embedding function (batch callable)
# ============================================================

class DummyEmbeddingFn:
    """LangChain-compatible embedding function, also callable for convenience."""
    def embed_documents(self, texts):
        import numpy as np
        dim = 128
        return [np.random.randn(dim).astype(np.float32).tolist() for _ in texts]

    def __call__(self, texts):
        """Allow direct function-call syntax for pre-computing embeddings."""
        return self.embed_documents(texts)

dummy_embed_fn = DummyEmbeddingFn()


# ============================================================
# FAISSClientWrapper tests
# ============================================================

class TestFAISSClientWrapper:
    """Test FAISS index building, loading, and management."""

    @pytest.fixture
    def client(self, tmp_path):
        """Create a FAISSClientWrapper using a temp directory."""
        return FAISSClientWrapper(index_dir=str(tmp_path))

    @pytest.fixture
    def sample_docs(self):
        return [
            FakeDocument("银灰是喀兰贸易的领袖", {"chunk_id": "chunk_1", "collection": "operators"}),
            FakeDocument("初雪是银灰的妹妹", {"chunk_id": "chunk_2", "collection": "operators"}),
            FakeDocument("崖心在罗德岛接受治疗", {"chunk_id": "chunk_3", "collection": "operators"}),
        ]

    def test_init_default_dir(self):
        """Default index_dir should be config.FAISS_INDEX_DIR."""
        client = FAISSClientWrapper()
        assert client.index_dir.exists()

    def test_init_custom_dir(self, tmp_path):
        client = FAISSClientWrapper(index_dir=str(tmp_path))
        assert client.index_dir == tmp_path

    def test_build_and_load_index(self, client, sample_docs):
        """Build index and verify it can be loaded."""
        embeddings = dummy_embed_fn([d.page_content for d in sample_docs])
        client.build_index("test_collection", sample_docs, embeddings=embeddings)

        result = client.load_index("test_collection")
        assert result is not None
        index, meta = result
        assert index.ntotal == 3
        assert len(meta) == 3
        assert meta[0]["page_content"] == "银灰是喀兰贸易的领袖"
        assert meta[0]["metadata"]["chunk_id"] == "chunk_1"

    def test_build_index_requires_embeddings_or_fn(self, client):
        """build_index raises if neither embeddings nor embedding_fn is given."""
        with pytest.raises(ValueError, match="Either embeddings or embedding_fn"):
            client.build_index("test", [FakeDocument("content")])

    def test_build_index_with_embedding_fn(self, client, sample_docs):
        """build_index should work with an embedding function."""
        client.build_index("test_ef", sample_docs, embedding_fn=dummy_embed_fn)
        result = client.load_index("test_ef")
        assert result is not None
        index, _ = result
        assert index.ntotal == 3

    def test_load_nonexistent_collection(self, client):
        result = client.load_index("nonexistent")
        assert result is None

    def test_get_chunk_count(self, client, sample_docs):
        embeddings = dummy_embed_fn([d.page_content for d in sample_docs])
        client.build_index("count_test", sample_docs, embeddings=embeddings)
        assert client.get_chunk_count("count_test") == 3

    def test_get_chunk_count_empty(self, client):
        assert client.get_chunk_count("no_such_collection") == 0

    def test_add_documents_new_collection(self, client, sample_docs):
        """add_documents to a non-existent collection creates it."""
        count = client.add_documents(
            "new_collection", sample_docs, embedding_fn=dummy_embed_fn
        )
        assert count == 3
        assert client.get_chunk_count("new_collection") == 3

    def test_add_documents_incremental(self, client, sample_docs):
        """add_documents to existing collection appends."""
        # First batch
        client.build_index("incr", sample_docs[:2], embedding_fn=dummy_embed_fn)
        assert client.get_chunk_count("incr") == 2

        # Second batch: append
        total = client.add_documents("incr", sample_docs[2:], embedding_fn=dummy_embed_fn)
        assert total == 3
        assert client.get_chunk_count("incr") == 3

    def test_add_documents_requires_embeddings(self, client):
        with pytest.raises(ValueError, match="Either embeddings or embedding_fn"):
            client.add_documents("x", [FakeDocument("content")])

    def test_metadata_saved_correctly(self, client, sample_docs):
        embeddings = dummy_embed_fn([d.page_content for d in sample_docs])
        client.build_index("meta_test", sample_docs, embeddings=embeddings)
        _, meta = client.load_index("meta_test")
        for i, doc in enumerate(sample_docs):
            assert meta[i]["page_content"] == doc.page_content
            assert meta[i]["metadata"]["chunk_id"] == doc.metadata["chunk_id"]

    def test_doc_without_chunk_id_gets_fallback(self, client):
        doc = FakeDocument("内容没有chunk_id", metadata={})
        embeddings = dummy_embed_fn([doc.page_content])
        client.build_index("fallback", [doc], embeddings=embeddings)
        _, meta = client.load_index("fallback")
        assert meta[0]["id"] == "doc_0"

    def test_index_file_exists(self, client, sample_docs):
        embeddings = dummy_embed_fn([d.page_content for d in sample_docs])
        client.build_index("file_test", sample_docs, embeddings=embeddings)
        idx_path = client._index_path("file_test")
        assert idx_path.exists()
        meta_path = client._meta_path("file_test")
        assert meta_path.exists()

    def test_index_dir_created(self, tmp_path):
        nested = tmp_path / "nested" / "index"
        client = FAISSClientWrapper(index_dir=str(nested))
        assert nested.exists()


# ============================================================
# T14: 向量数 / 文档数 一致性校验（错位索引会让 metadata 整体串号）
# ============================================================

def _embeddings(count, dim=128, seed=7):
    """确定性嵌入：同一 seed 生成同一批向量，便于断言文件未被改动。"""
    rng = np.random.default_rng(seed)
    return rng.standard_normal((count, dim)).astype(np.float32).tolist()


def _stray_files(client) -> list:
    """索引目录里除正式 index/meta 之外的残留文件（临时文件、锁文件）。"""
    names = []
    for p in client.index_dir.iterdir():
        if p.name.endswith((".tmp", ".lock")) or ".tmp-" in p.name:
            names.append(p.name)
    return sorted(names)


class TestBuildIndexValidation:
    """build_index/add_documents 的向量数与文档数一致性校验。

    防的回归：旧实现直接 `index.add(vectors)` 再按 documents 建 metadata，
    嵌入接口少返回/多返回一条向量时索引就会「n+m 个向量配 n 条元数据」，
    检索命中后取到的是别人的正文，且错误索引已经落盘、后续一直被读到。
    """

    @pytest.fixture
    def client(self, tmp_path):
        return FAISSClientWrapper(index_dir=str(tmp_path))

    @pytest.fixture
    def sample_docs(self):
        return [FakeDocument(f"正文{i}", {"chunk_id": f"chunk_{i}"}) for i in range(3)]

    def test_count_mismatch_raises_without_writing_anything(self, client, sample_docs):
        """向量数 2 ≠ 文档数 3：必须抛可读异常，且一个文件都不落盘。"""
        with pytest.raises(ValueError, match="Embedding/document count mismatch"):
            client.build_index("misaligned", sample_docs, embeddings=_embeddings(2))

        assert not client._index_path("misaligned").exists()
        assert not client._meta_path("misaligned").exists()
        assert _stray_files(client) == []          # 失败路径不留临时文件
        assert list(client.index_dir.iterdir()) == []

    def test_failed_rebuild_keeps_previous_index_intact(self, client, sample_docs):
        """重建失败不能破坏已有索引：旧索引的向量/元数据必须原样可用。

        防的回归：旧实现是「先覆盖写 index 再覆盖写 meta」，第二次写失败（或
        校验发生在写之后）会留下错位文件，把上一版完好的索引也一起毁掉。
        """
        client.build_index("keep", sample_docs, embeddings=_embeddings(3, seed=1))
        idx_bytes = client._index_path("keep").read_bytes()
        meta_bytes = client._meta_path("keep").read_bytes()

        with pytest.raises(ValueError, match="Embedding/document count mismatch"):
            client.build_index("keep", sample_docs, embeddings=_embeddings(2, seed=2))

        assert client._index_path("keep").read_bytes() == idx_bytes
        assert client._meta_path("keep").read_bytes() == meta_bytes
        index, meta = client.load_index("keep")
        assert index.ntotal == 3 and len(meta) == 3
        assert _stray_files(client) == []

    def test_empty_embeddings_rejected(self, client, sample_docs):
        """嵌入接口返回空列表：必须报错而不是建一个 0 向量的索引。"""
        with pytest.raises(ValueError, match="no embeddings"):
            client.build_index("empty_emb", sample_docs, embeddings=[])
        assert not client._index_path("empty_emb").exists()

    def test_empty_documents_rejected(self, client):
        """空文档列表：视为调用方 bug，直接拒绝。"""
        with pytest.raises(ValueError, match="documents is empty"):
            client.build_index("empty_docs", [], embeddings=[])

    @pytest.mark.parametrize("bad_embeddings", [
        [[0.1] * 4, [0.2] * 4, [0.3] * 5],   # 维度不一致
        [[], [], []],                        # 零维向量
        [[0.1] * 4, [], [0.3] * 4],          # 混入零维
    ])
    def test_inconsistent_dimensions_rejected(self, client, sample_docs, bad_embeddings):
        """向量维度不一致/为 0：np.array 会退化成 object 数组，必须在建索引前拦下。"""
        with pytest.raises(ValueError, match="Invalid embedding dimensions"):
            client.build_index("bad_dim", sample_docs, embeddings=bad_embeddings)
        assert not client._index_path("bad_dim").exists()

    def test_add_documents_rejects_misaligned_batch(self, client, sample_docs):
        """追加批次少返回向量：拒绝追加，且已有索引的向量数不变（不丢已有数据）。"""
        client.build_index("append", sample_docs[:2], embeddings=_embeddings(2))
        # 1 篇文档却拿到 2 条向量 —— 追加会让 meta 与向量错位
        with pytest.raises(ValueError, match="misaligned batch"):
            client.add_documents("append", sample_docs[2:], embeddings=_embeddings(2))
        index, meta = client.load_index("append")
        assert index.ntotal == 2 and len(meta) == 2
        assert index.ntotal == len(meta)

    def test_to_langchain_faiss_rejects_index_meta_mismatch(self, client, sample_docs):
        """index.ntotal != len(meta) 时必须拒绝加载（否则 chunk 与向量整体错位）。

        构造方式：正常建索引后，只给 index 追加一条向量而不动 meta，模拟
        「写入中途失败 / 旧版本留下的错位文件」。
        """
        import faiss

        client.build_index("tampered", sample_docs, embeddings=_embeddings(3))
        index, meta = client.load_index("tampered")
        index.add(np.array(_embeddings(1), dtype=np.float32))
        faiss.write_index(index, str(client._index_path("tampered")))

        assert index.ntotal == 4 and len(meta) == 3       # 前置条件
        with pytest.raises(ValueError, match="index/meta mismatch"):
            client.to_langchain_faiss("tampered", dummy_embed_fn)

    def test_to_langchain_faiss_still_loads_consistent_index(self, client, sample_docs):
        """反向对照：一致的索引必须能正常加载，避免上面那条变成「无条件抛错」。"""
        client.build_index("consistent", sample_docs, embeddings=_embeddings(3))
        vs = client.to_langchain_faiss("consistent", dummy_embed_fn)
        assert vs is not None
        assert len(vs.docstore._dict) == 3


# ============================================================
# T26: 并发/重复 add_documents 不丢更新
# ============================================================

class TestConcurrentAddDocuments:
    """load→add→write 必须整体串行化。

    防的回归：旧实现无锁，两个线程各自读到同一份旧索引再分别写回，
    后写者覆盖先写者 —— 表现为「追加了 N 条但只多出 M 条」的静默丢更新。
    """

    @pytest.fixture
    def client(self, tmp_path):
        return FAISSClientWrapper(index_dir=str(tmp_path))

    def _doc(self, i, tag="d"):
        return FakeDocument(f"正文-{tag}-{i}", {"chunk_id": f"{tag}-{i}"})

    def test_repeated_sequential_add_documents_accumulates(self, client):
        """连续追加同一批文档 5 次：每次都必须真的落进去（不覆盖上一次）。"""
        client.build_index("seq", [self._doc(0, "base")], embeddings=_embeddings(1))
        for round_no in range(5):
            total = client.add_documents(
                "seq",
                [self._doc(round_no * 2 + 1, "a"), self._doc(round_no * 2 + 2, "b")],
                embeddings=_embeddings(2, seed=round_no + 1),
            )
            assert total == 1 + (round_no + 1) * 2

        index, meta = client.load_index("seq")
        assert index.ntotal == 11
        assert len(meta) == 11
        assert index.ntotal == len(meta)
        assert meta[10]["metadata"]["chunk_id"] == "b-10"

    def test_concurrent_add_documents_keeps_every_update(self, client):
        """4 线程并发追加各 3 条：最终必须是 1 + 12 = 13 条且 meta 一一对应。"""
        import threading

        client.build_index("conc", [self._doc(0, "base")], embeddings=_embeddings(1))
        errors = []
        barrier = threading.Barrier(4, timeout=10)

        def worker(k):
            try:
                barrier.wait()   # 尽量让 4 个线程同时进入 load→add→write
                client.add_documents(
                    "conc",
                    [self._doc(k * 10 + i, "w") for i in range(3)],
                    embeddings=_embeddings(3, seed=k + 1),
                )
            except Exception as exc:      # noqa: BLE001 - 线程内异常要带回主线程断言
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert errors == []
        assert not any(t.is_alive() for t in threads)
        index, meta = client.load_index("conc")
        assert index.ntotal == 13
        assert len(meta) == 13
        expected_ids = {"base-0"} | {f"w-{k * 10 + i}" for k in range(4) for i in range(3)}
        assert {m["id"] for m in meta.values()} == expected_ids
        assert _stray_files(client) == []     # 锁文件/临时文件都已清理
