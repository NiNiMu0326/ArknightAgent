import pickle
from typing import List
from pathlib import Path
from rank_bm25 import BM25Okapi
import jieba

BASE_DIR = Path(__file__).parent.parent.parent
CHUNKS_DIR = BASE_DIR / "chunks"


def _tokenize(text: str) -> List[str]:
    """Tokenize Chinese text with jieba, filtering out whitespace-only tokens."""
    return [t for t in jieba.cut(text) if t.strip()]


class BM25Indexer:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.bm25 = None
        self.corpus = []
        self.corpus_ids = []  # List of chunk IDs (can be file paths or indices)
        self.corpus_size = 0
        self.doc_lengths = []
        self.avgdl = 0

    def build(self, corpus: List[str], corpus_ids: List[str] = None):
        """Build BM25 index from list of document texts.

        Args:
            corpus: List of document texts
            corpus_ids: List of corresponding chunk IDs (optional, defaults to indices)
        """
        self.corpus = corpus
        self.corpus_size = len(corpus)
        tokenized_corpus = [_tokenize(doc) for doc in corpus]
        self.bm25 = BM25Okapi(tokenized_corpus, k1=self.k1, b=self.b)
        self.doc_lengths = [len(tokens) for tokens in tokenized_corpus]
        self.avgdl = sum(self.doc_lengths) / len(self.doc_lengths) if self.doc_lengths else 0
        # Build corpus_ids if not provided (use indices as fallback)
        if corpus_ids is None:
            self.corpus_ids = [f"doc_{i}" for i in range(len(corpus))]
        else:
            self.corpus_ids = corpus_ids

    def retrieve(self, query: str, top_k: int = 10) -> List[int]:
        """Retrieve top-k document indices for a query."""
        if not self.bm25 or not query:
            return []
        tokenized_query = _tokenize(query)
        scores = self.bm25.get_scores(tokenized_query)
        # 过滤零分文档，避免完全不相关的 chunk 进入 RRF 融合污染结果
        positive = [(i, score) for i, score in enumerate(scores) if score > 0]
        positive.sort(key=lambda item: item[1], reverse=True)
        return [i for i, _ in positive[:top_k]]

    def save(self, path: str):
        """Save index to file."""
        with open(path, 'wb') as f:
            pickle.dump({
                'bm25': self.bm25,
                'corpus': self.corpus,
                'corpus_ids': getattr(self, 'corpus_ids', [f"doc_{i}" for i in range(len(self.corpus))]),
                'corpus_size': self.corpus_size,
                'doc_lengths': self.doc_lengths,
                'avgdl': self.avgdl,
                'k1': self.k1,
                'b': self.b
            }, f)

    def add_documents(self, new_corpus: List[str], new_corpus_ids: List[str] = None):
        """增量添加新文档到已有索引。

        BM25 的全局统计（IDF、平均文档长度）依赖全部文档，
        因此需要重建 BM25Okapi，但不涉及 API 调用，速度很快。
        """
        if not new_corpus:
            return

        self.corpus.extend(new_corpus)
        self.corpus_size = len(self.corpus)

        if new_corpus_ids:
            self.corpus_ids.extend(new_corpus_ids)
        else:
            start = len(self.corpus_ids)
            self.corpus_ids.extend([f"doc_{start + i}" for i in range(len(new_corpus))])

        # Rebuild BM25 with full corpus
        tokenized_corpus = [_tokenize(doc) for doc in self.corpus]
        self.bm25 = BM25Okapi(tokenized_corpus, k1=self.k1, b=self.b)
        self.doc_lengths = [len(t) for t in tokenized_corpus]
        self.avgdl = sum(self.doc_lengths) / len(self.doc_lengths) if self.doc_lengths else 0

    @classmethod
    def load(cls, path: str) -> 'BM25Indexer':
        """Load index from file."""
        with open(path, 'rb') as f:
            data = pickle.load(f)
        indexer = cls(k1=data['k1'], b=data['b'])
        indexer.bm25 = data['bm25']
        indexer.corpus = data['corpus']
        indexer.corpus_ids = data.get('corpus_ids', [f"doc_{i}" for i in range(len(data['corpus']))])
        indexer.corpus_size = data['corpus_size']
        indexer.doc_lengths = data['doc_lengths']
        indexer.avgdl = data['avgdl']
        return indexer


def build_collection_bm25(collection_name: str, chunks_dir: str = None) -> BM25Indexer:
    """Build BM25 index for a collection from chunk files."""
    if chunks_dir is None:
        chunks_dir = CHUNKS_DIR / collection_name
    else:
        chunks_dir = Path(chunks_dir)

    chunk_paths = []
    for ext in ['*.md', '*.txt']:
        chunk_paths.extend(list(chunks_dir.glob(ext)))
    chunk_paths.sort()

    corpus = []
    corpus_ids = []
    for path in chunk_paths:
        with open(path, 'r', encoding='utf-8') as f:
            corpus.append(f.read())
        # Use path.stem as chunk_id (consistent with FAISS index metadata)
        corpus_ids.append(path.stem)

    indexer = BM25Indexer()
    indexer.build(corpus, corpus_ids)
    return indexer, corpus


def build_all_bm25_indexes():
    """Build BM25 indexes for all 3 collections."""
    collections = {
        'operators': CHUNKS_DIR / 'operators',
        'stories': CHUNKS_DIR / 'stories',
        'knowledge': CHUNKS_DIR / 'knowledge'
    }

    for name, dir_path in collections.items():
        if not dir_path.exists():
            print(f"Skipping {name} - directory not found")
            continue

        indexer, corpus = build_collection_bm25(name, dir_path)
        out_path = CHUNKS_DIR / f'{name}_bm25.pkl'
        indexer.save(str(out_path))
        print(f"Built {name} BM25 index: {len(corpus)} documents -> {out_path}")


if __name__ == "__main__":
    build_all_bm25_indexes()
