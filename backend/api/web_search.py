"""
Web search client using Tavily + DuckDuckGo Lite fallback.
Independent of any specific LLM/embedding provider.
"""

import re
import logging
from typing import List, Dict

from backend import config
from backend.api.base import create_http_session

logger = logging.getLogger(__name__)

# Module-level session with connection pooling and retry
_session = create_http_session()


def search(query: str, limit: int = 5) -> List[Dict[str, str]]:
    """Perform web search. Tries Tavily first, falls back to DuckDuckGo Lite.

    Args:
        query: Search query string.
        limit: Maximum number of results to return.

    Returns:
        List of dicts with "title", "url", "snippet" keys.
    """
    tavily_key = getattr(config, 'TAVILY_API_KEY', None)
    if tavily_key:
        try:
            results = _search_tavily(query, limit, tavily_key)
            # Tavily 正常返回空结果时也应尝试 DDG 兜底
            if results:
                return results
            logger.info("Tavily returned no results, falling back to DuckDuckGo")
        except Exception as e:
            logger.warning(f"Tavily search failed: {e}, falling back to DuckDuckGo")

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
