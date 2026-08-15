"""
SiliconFlow API client for embedding and reranking.
Used for bge-m3 embeddings and bge-reranker-v2-m3 reranking.
"""

from typing import List, Dict, Any

from backend import config
from backend.api.base import create_http_session

# 共享连接池：SiliconFlowClient 每次请求都会新建，复用同一个 Session
# 才能让 urllib3 的连接池/重试配置真正生效
_shared_session = create_http_session()


class SiliconFlowClient:
    """Client for SiliconFlow API calls with connection pooling and retry logic."""

    def __init__(self, api_key: str = None):
        """
        Initialize SiliconFlow client.

        Args:
            api_key: SiliconFlow API key. If not provided, loads from config.SILICONFLOW_API_KEY.
        """
        self.api_key = api_key or config.SILICONFLOW_API_KEY
        if not self.api_key:
            raise ValueError("API key must be provided or set in SILICONFLOW_API_KEY environment variable.")
        self.base_url = config.SILICONFLOW_BASE_URL
        self.embedding_model = config.EMBEDDING_MODEL
        self.reranker_model = config.RERANKER_MODEL
        # Reuse the module-level session (connection pooling + retry)
        self._session = _shared_session

    def embed(self, texts: List[str], model: str = None) -> List[List[float]]:
        """
        Generate embeddings for texts.

        Args:
            texts: List of text strings to embed.
            model: Embedding model to use. Defaults to config EMBEDDING_MODEL.

        Returns:
            List of embedding vectors (each vector is a list of floats).
        """
        if not texts:
            return []
        model = model or self.embedding_model
        url = f"{self.base_url}/embeddings"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "input": texts
        }

        response = self._session.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()

        try:
            result = response.json()
        except ValueError:
            raise Exception(f"Invalid JSON response from {url}: {response.text[:200]}")
        return [item["embedding"] for item in result["data"]]

    def rerank(self, query: str, documents: List[str], model: str = None) -> List[Dict[str, Any]]:
        """
        Rerank documents based on query relevance.

        Args:
            query: The query string.
            documents: List of document strings to rerank.
            model: Reranker model to use. Defaults to config RERANKER_MODEL.

        Returns:
            List of dicts with "index" and "relevance_score" keys.
        """
        if not documents:
            return []
        model = model or self.reranker_model
        url = f"{self.base_url}/rerank"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "query": query,
            "documents": documents
        }

        response = self._session.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()

        try:
            result = response.json()
        except ValueError:
            raise Exception(f"Invalid JSON response from {url}: {response.text[:200]}")
        return result["results"]

    def chat(self, messages: List[Dict[str, str]], model: str = None,
             temperature: float = 0.1, max_tokens: int = 4096) -> str:
        """
        Call SiliconFlow Chat Completions API (OpenAI-compatible).

        Args:
            messages: List of message dicts with 'role' and 'content'.
            model: Model name. Defaults to Qwen/Qwen3-32B.
            temperature: Sampling temperature.
            max_tokens: Max tokens to generate.

        Returns:
            The model's text response.
        """
        model = model or "Qwen/Qwen3-32B"
        url = f"{self.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        response = self._session.post(url, headers=headers, json=payload, timeout=300)
        response.raise_for_status()

        try:
            result = response.json()
        except ValueError:
            raise Exception(f"Invalid JSON response from {url}: {response.text[:200]}")
        return result["choices"][0]["message"]["content"]

