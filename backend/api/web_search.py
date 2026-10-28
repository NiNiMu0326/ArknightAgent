"""
Web search client with a three-stage fallback chain.

Provider order (each stage only runs when the previous one fails or returns
nothing):
    1. Tavily           -- needs TAVILY_API_KEY (free tier: 1000 requests/month)
    2. DeepSeek Search  -- needs DEEPSEEK_API_KEY (official self-built engine,
                           billed per successful request, 5 CNY / 1000 requests)
    3. DuckDuckGo Lite  -- no key needed, HTML scraping, last resort

The fallback is handled entirely inside this module: one web_search tool is
exposed to the LLM, so the model never sees (or has to choose between)
multiple search providers. Independent of any specific LLM/embedding provider.
"""

import re
import logging
from typing import List, Dict

from backend import config
from backend.api.base import create_http_session

logger = logging.getLogger(__name__)

# Module-level session with connection pooling and retry
_session = create_http_session()

# DeepSeek Search API limits (see https://api-docs.deepseek.com/zh-cn/api/search)
_DEEPSEEK_QUERY_MAX_CHARS = 128
_DEEPSEEK_COUNT_MAX = 50


def search(query: str, limit: int = 5) -> List[Dict[str, str]]:
    """Perform web search: Tavily -> DeepSeek Search -> DuckDuckGo Lite.

    Each provider is tried only if the previous one raised or returned an empty
    list, so a quota-exhausted Tavily key degrades transparently instead of
    handing nothing back to the agent.

    Args:
        query: Search query string.
        limit: Maximum number of results to return.

    Returns:
        List of dicts with "title", "url", "snippet" keys.
    """
    # --- Stage 1: Tavily ---
    tavily_key = getattr(config, 'TAVILY_API_KEY', None)
    if tavily_key:
        try:
            results = _search_tavily(query, limit, tavily_key)
            # Tavily 正常返回空结果时也应尝试后续兜底
            if results:
                return results
            logger.info("Tavily returned no results, falling back to DeepSeek Search")
        except Exception as e:
            logger.warning(f"Tavily search failed: {e}, falling back to DeepSeek Search")

    # --- Stage 2: DeepSeek Search（官方自研搜索引擎）---
    deepseek_key = getattr(config, 'DEEPSEEK_API_KEY', None)
    if deepseek_key and getattr(config, 'DEEPSEEK_SEARCH_ENABLED', True):
        try:
            results = _search_deepseek(query, limit, deepseek_key)
            if results:
                return results
            logger.info("DeepSeek search returned no results, falling back to DuckDuckGo")
        except Exception as e:
            logger.warning(f"DeepSeek search failed: {e}, falling back to DuckDuckGo")

    # --- Stage 3: DuckDuckGo Lite（最后兜底，异常照旧向上抛）---
    return _search_duckduckgo(query, limit)


def _search_tavily(query: str, limit: int, api_key: str) -> List[Dict[str, str]]:
    """Search using Tavily API."""
    url = "https://api.tavily.com/search"
    headers = {"Content-Type": "application/json"}
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "max_results": limit,
    }
    response = _session.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    tavily_results = result.get("results", [])
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in tavily_results[:limit]
    ]


def _search_deepseek(query: str, limit: int, api_key: str) -> List[Dict[str, str]]:
    """Search using the DeepSeek official Search API (POST /v1/tools/search).

    Bearer auth reuses the DeepSeek API key. need_content stays False: the agent
    only consumes a short snippet, and skipping full page bodies keeps the
    payload small (billing is per request, not per byte).

    HTTP 408/429/5xx are retried by the shared session; other 4xx are permanent
    (401 bad key, 402 insufficient balance, 403/404 rejected) and surface as an
    exception so that search() can fall through to DuckDuckGo.
    """
    url = getattr(config, 'DEEPSEEK_SEARCH_URL', 'https://api.deepseek.com/v1/tools/search')
    timeout = float(getattr(config, 'DEEPSEEK_SEARCH_TIMEOUT', 20.0) or 20.0)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    query = (query or "").strip()
    if len(query) > _DEEPSEEK_QUERY_MAX_CHARS:
        logger.info(f"DeepSeek search query truncated to {_DEEPSEEK_QUERY_MAX_CHARS} chars")
        query = query[:_DEEPSEEK_QUERY_MAX_CHARS]
    payload = {
        "query": query,
        "count": max(1, min(int(limit), _DEEPSEEK_COUNT_MAX)),
        "need_content": False,
    }
    response = _session.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    body = response.json()

    results: List[Dict[str, str]] = []
    for r in body.get("results", [])[:limit]:
        # snippet (~100-200 chars) is for front-end display; summary (~600-800
        # chars) is the model-oriented text, used only when snippet is empty.
        snippet = r.get("snippet") or r.get("summary") or ""
        results.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": snippet,
        })
    logger.info(f"[DeepSeek Search] got {len(results)} results for: {query}")
    return results


def _search_duckduckgo(query: str, limit: int) -> List[Dict[str, str]]:
    """Search using DuckDuckGo Lite HTML (no API key needed, free)."""
    url = "https://lite.duckduckgo.com/lite/"
    try:
        resp = _session.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        vqd_match = re.search(r'name="vqd"\s+value="([^"]+)"', resp.text)
        if not vqd_match:
            vqd_match = re.search(r'vqd=([^&"]+)', resp.text)
        vqd = vqd_match.group(1) if vqd_match else ""

        data = {"q": query, "vqd": vqd, "kl": "wt-wt", "l": "wt-wt"}
        resp = _session.post(url, data=data, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        html = resp.text

        results = []
        result_blocks = re.findall(
            r'<a[^>]+class="result-link"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
            html, re.DOTALL,
        )
        for href, title_html, snippet_html in result_blocks[:limit]:
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            snippet = re.sub(r'<[^>]+>', '', snippet_html).strip()
            if title and snippet:
                results.append({"title": title, "url": href, "snippet": snippet})

        if not results:
            links = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)
            for href, title_html in links[:limit]:
                title = re.sub(r'<[^>]+>', '', title_html).strip()
                if title and len(title) > 5 and "duckduckgo" not in href.lower():
                    results.append({"title": title, "url": href, "snippet": ""})

        logger.info(f"[DuckDuckGo] got {len(results)} results for: {query}")
        return results[:limit]

    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}")
        return []
