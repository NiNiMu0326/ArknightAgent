"""
SiliconFlow Cross-Encoder Reranker as LangChain BaseDocumentCompressor.
"""
import logging
import math
from typing import List, Optional, Sequence
from langchain_core.documents import Document
from langchain_core.documents.compressor import BaseDocumentCompressor
from langchain_core.callbacks.manager import Callbacks
from pydantic import Field
from backend import config
from backend.api.siliconflow import SiliconFlowClient

logger = logging.getLogger(__name__)


class SiliconFlowReranker(BaseDocumentCompressor):
    """SiliconFlow bge-reranker cross-encoder for document reranking."""

    api_key: str = Field(default="")
    model: str = Field(default="BAAI/bge-reranker-v2-m3")
    top_n: int = Field(default=5)

    class Config:
        arbitrary_types_allowed = True
        # 允许额外属性（如 _client）
        extra = "allow"

    def __init__(self, api_key: str = None, top_n: int = 5, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key or config.SILICONFLOW_API_KEY
        self.top_n = top_n
        self._client = SiliconFlowClient(api_key=self.api_key)

    def __repr__(self) -> str:
        """显式 repr，避免 api_key 被日志/异常栈明文打印。

        本类继承自 langchain 的 pydantic v1 BaseModel，而本模块 import 的是
        pydantic v2 的 Field —— v1 元类不识别 v2 FieldInfo，只会把它当默认值，
        因此 ``Field(repr=False)`` 在这里完全不生效，只能自己实现 __repr__。
        """
        return f"{self.__class__.__name__}(top_n={self.top_n})"

    def dict(self, **kwargs) -> dict:
        """序列化时剔除 api_key，避免被日志/链路追踪 dump 出明文密钥。"""
        data = super().dict(**kwargs)
        data.pop("api_key", None)
        return data

    def _fallback(self, doc_with_idx: List[Document]) -> List[Document]:
        """重排不可用时的兜底：按原始检索顺序返回前 top_n 条"""
        fallback = []
        for doc in doc_with_idx[:self.top_n]:
            new_metadata = dict(doc.metadata)
            new_metadata["relevance_score"] = 0.0
            fallback.append(
                Document(page_content=doc.page_content, metadata=new_metadata)
            )
        return fallback

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Optional[Callbacks] = None,
    ) -> List[Document]:
        """Rerank documents and return top_n with relevance_score in metadata."""
        if not documents:
            return []

        # 保存原始索引，便于后续去重
        doc_with_idx = []
        for i, doc in enumerate(documents):
            new_doc = Document(
                page_content=doc.page_content,
                metadata=dict(doc.metadata) if doc.metadata else {}
            )
            new_doc.metadata["original_index"] = i
            doc_with_idx.append(new_doc)

        doc_texts = [doc.page_content for doc in doc_with_idx]
        raw_results = self._client.rerank(query, doc_texts)

        # 接口异常时可能返回 None / 非列表 / 空列表（SiliconFlowClient 失败即 return []），
        # 直接使用会抛 AttributeError 让整条 RAG 链路失败，这里回退原始检索顺序。
        if not isinstance(raw_results, list) or not raw_results:
            logger.warning(
                "重排返回结果无效（%s），回退原始检索顺序",
                type(raw_results).__name__,
            )
            return self._fallback(doc_with_idx)

        # 归一化后再排序：relevance_score 为 None 时与 float 比较会抛 TypeError，
        # index 非整数时与 len() 比较同样会抛 TypeError，整条链路直接失败。
        scored = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index", 0))
            except (TypeError, ValueError):
                continue
            try:
                score = float(item.get("relevance_score"))
            except (TypeError, ValueError):
                score = 0.0
            if not math.isfinite(score):
                score = 0.0
            scored.append((score, idx))

        if not scored:
            logger.warning("重排结果全部无法解析，回退原始检索顺序")
            return self._fallback(doc_with_idx)

        # Sort by relevance_score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # 去重：基于 page_content 去重，保留第一个（最高分）的结果
        seen_content: set = set()
        reranked = []
        for score, idx in scored:
            if not 0 <= idx < len(doc_with_idx):
                continue
            doc = doc_with_idx[idx]
            # 使用 chunk_id 或内容前100字符作为去重键
            chunk_id = doc.metadata.get("chunk_id", "")
            dedup_key = chunk_id if chunk_id else doc.page_content[:100]

            if dedup_key in seen_content:
                continue
            seen_content.add(dedup_key)

            new_metadata = dict(doc.metadata)
            new_metadata["relevance_score"] = score
            reranked.append(
                Document(
                    page_content=doc.page_content,
                    metadata=new_metadata,
                )
            )
            if len(reranked) >= self.top_n:
                break

        if not reranked:
            # 有可解析结果但索引全部越界，等同于重排失败，同样回退
            logger.warning("重排索引全部越界，回退原始检索顺序")
            return self._fallback(doc_with_idx)
        return reranked
