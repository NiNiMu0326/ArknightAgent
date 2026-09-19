import logging
import re
import threading
import time
from pathlib import Path
from typing import List, Dict, Optional

from backend.rag.cache import LRUCache

logger = logging.getLogger(__name__)

# 进程级共享缓存：ParentDocumentRetriever 每次工具调用都会新建实例，
# 若缓存挂在实例上会在请求结束后立即失效，导致每次检索都重新扫描/读取文件。
_PARENT_DOC_CACHE = LRUCache(max_size=100, ttl_seconds=18000)
_PARENT_DOC_CACHE_LOCK = threading.Lock()
_SOURCE_INDEX_CACHE: Dict[str, tuple] = {}
_SOURCE_INDEX_CACHE_LOCK = threading.Lock()
_SOURCE_INDEX_CACHE_TTL = 3600  # 1 hour TTL

# FAISS 构建时把 chunk 文件名（如 operators_0001_01.md）写进了 source_file，
# 但真正的父文档位于 data/{source}/ 下。命中这种元数据时改用 chunk_id 反查父文件。
_CHUNK_FILENAME_RE = re.compile(r"^(operators|stories)_\d{4}(?:_\d{2})*\.(?:md|txt)$")


def _looks_like_chunk_filename(source_file: str, source: str) -> bool:
    """Return True if source_file is a chunk artifact name for the given source."""
    return bool(_CHUNK_FILENAME_RE.match(source_file)) and source_file.startswith(f"{source}_")


def _resolve_within(source_dir: Path, source_file: str) -> Optional[Path]:
    """把 metadata 里的 source_file 解析为 source_dir 下的真实路径。

    安全边界：source_file 直接来自 chunk metadata（可被外部写入），
    因此必须拒绝绝对路径、'../' 逃逸与指向目录外的符号链接。
    非法时返回 None，由调用方回退到 chunk 内容。
    """
    if not source_file or Path(source_file).is_absolute():
        return None
    try:
        candidate = (source_dir / source_file).resolve()
        base = source_dir.resolve()
        candidate.relative_to(base)
    except (ValueError, OSError):
        return None
    return candidate


class ParentDocumentRetriever:
    def __init__(self, chunks_dir: str = None, data_dir: str = None):
        from backend import config as _cfg
        self.chunks_dir = chunks_dir or str(_cfg.CHUNKS_DIR)
        self.data_dir = data_dir or str(_cfg.DATA_DIR)
        # Backward-compatible alias for tests; actual cache is process-wide above.
        self._doc_cache = _PARENT_DOC_CACHE

    def _build_source_index(self, source: str, cache_attr: str = "", ts_attr: str = "") -> Dict[int, str]:
        """Build mapping from document index to source filename.

        Files are sorted alphabetically and indexed starting from 1.
        The cache is process-wide (keyed by resolved source directory), because
        this retriever is instantiated on every tool call.
        """
        source_dir = Path(self.data_dir) / source
        cache_key = str(source_dir.resolve())

        # 无锁快路径：命中未过期缓存直接返回，避免每次调用都抢锁
        cached = _SOURCE_INDEX_CACHE.get(cache_key)
        if cached is not None:
            cached_index, cached_ts = cached
            if time.time() - cached_ts < _SOURCE_INDEX_CACHE_TTL:
                return cached_index

        # 慢路径：锁内二次确认后再扫描并写回。旧实现是「锁内读缓存 → 释放锁 →
        # 扫描 → 再抢锁写回」，多线程首次命中同一 source 时会重复扫描，
        # 后写者还可能用较旧的结果覆盖较新的索引。
        with _SOURCE_INDEX_CACHE_LOCK:
            cached = _SOURCE_INDEX_CACHE.get(cache_key)
            if cached is not None:
                cached_index, cached_ts = cached
                if time.time() - cached_ts < _SOURCE_INDEX_CACHE_TTL:
                    return cached_index

            # 目录不存在（或没有文件）时返回空索引但不写缓存：
            # 负缓存会把一次暂时性故障放大成整个 TTL 内的持续失败
            if not source_dir.exists():
                return {}

            files = sorted([f.name for f in source_dir.glob('*.md') if f.name.endswith('.md')])
            index = {i + 1: f for i, f in enumerate(files)}
            if not index:
                return {}

            _SOURCE_INDEX_CACHE[cache_key] = (index, time.time())
            return index


    def _get_parent_file(self, chunk_id: str, source: str) -> str:
        """Map a chunk_id to its source file name.

        For operators/stories chunks:
        - Extract the base index from chunk_id (e.g., operators_0001_01 -> 0001)
        - Look up the source file using the built index

        Args:
            chunk_id: e.g., 'operators_0001_01' or 'stories_0001_01'
            source: 'operators' or 'stories'

        Returns:
            Source filename, e.g., 'char_002_amiya.md'
        """
        # Parse chunk_id to extract base index
        # Format: source_XXXX or source_XXXX_YY or source_XXXX_YY_ZZ
        parts = chunk_id.split('_')
        if len(parts) < 2:
            return None

        try:
            base_idx = int(parts[1])
        except ValueError:
            return None

        if source == 'operators':
            index_map = self._build_source_index('operators', '_operators_index_cache', '_operators_index_timestamp')
        elif source == 'stories':
            index_map = self._build_source_index('stories', '_stories_index_cache', '_stories_index_timestamp')
        else:
            return None

        return index_map.get(base_idx)

    def get_parent_content(self, chunk: Dict, source: str) -> str:
        """Get the full parent document content for a chunk.

        Args:
            chunk: Chunk dict with chunk_id, metadata, etc.
            source: 'operators' or 'stories'

        Returns:
            Full content of the parent document, or chunk content if not found.
        """
        metadata = chunk.get('metadata', {})
        chunk_id = chunk.get('chunk_id', '')
        source_file = metadata.get('source_file', '')

        # Build path to original source
        if source == 'operators':
            source_dir = Path(self.data_dir) / 'operators'
        elif source == 'stories':
            source_dir = Path(self.data_dir) / 'stories'
        else:
            return chunk.get('content', '')

        # FAISS 索引把 chunk 文件名写进了 source_file，真正的父文档在 data/{source}/ 下，
        # 这里无法命中时要回退到 chunk_id -> data 文件映射。
        # 校验通过解析后的路径做（source_file 来自 metadata，不可信）。
        source_path = _resolve_within(source_dir, source_file) if source_file else None
        if source_path is not None and source_path.exists():
            pass
        elif source_file and _looks_like_chunk_filename(source_file, source):
            derived = self._get_parent_file(chunk_id, source)
            if derived:
                source_file = derived
        elif not source_file:
            source_file = self._get_parent_file(chunk_id, source)

        if not source_file:
            return chunk.get('content', '')

        source_path = _resolve_within(source_dir, source_file)
        if source_path is None:
            logger.warning(
                "[PARENT_DOC] Rejected out-of-scope source_file=%r for source=%s",
                source_file, source,
            )
            return chunk.get('content', '')

        cache_key = str(source_path)
        with _PARENT_DOC_CACHE_LOCK:
            cached = self._doc_cache.get(cache_key)
        if cached is not None:
            return cached

        # exists() 与 open() 之间文件可能被删除/轮转，或权限不足、编码非法；
        # 本方法的契约是「读不到就回退到 chunk 内容」，因此这里吞掉读取异常。
        try:
            with open(source_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except OSError as exc:
            logger.warning("[PARENT_DOC] Failed to read %s: %s", source_path, exc)
            return chunk.get('content', '')
        except UnicodeDecodeError as exc:
            logger.warning("[PARENT_DOC] Undecodable content in %s: %s", source_path, exc)
            return chunk.get('content', '')

        with _PARENT_DOC_CACHE_LOCK:
            self._doc_cache.set(cache_key, content)
        return content

    def retrieve_parent_docs(self, chunks: List[Dict], source: str) -> List[Dict]:
        """Take a list of chunk results and return full parent documents.

        Args:
            chunks: List of chunk dicts from search results
            source: 'operators' or 'stories'

        Returns:
            List of dicts with chunk_id, parent_content, metadata, score, source
        """
        results = []
        for chunk in chunks:
            parent_content = self.get_parent_content(chunk, source)
            results.append({
                'chunk_id': chunk.get('chunk_id', ''),
                'parent_content': parent_content,
                'metadata': chunk.get('metadata', {}),
                'score': chunk.get('score', 0.0),
                'source': source,
                'section': chunk.get('metadata', {}).get('section', '')
            })
        return results
