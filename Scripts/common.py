"""
脚本共享工具
============
PRTS 请求头与 MediaWiki 分页抓取等公共逻辑。

2026-08 起 PRTS 的 Tengine WAF 会拦截缺少 Accept / Accept-Language
头的请求（返回 403 Forbidden），所有访问 prts.wiki 的脚本必须使用
PRTS_HEADERS，不要再单独写裸 User-Agent。
"""

import sys

import requests

PRTS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

PRTS_API_URL = "https://prts.wiki/api.php"

# MediaWiki 分类成员单页上限：匿名请求 cmlimit 最高 500（请求更大值会被静默降到 500）
CMLIMIT = 500

# 翻页页数上限（500 × 60 = 30000 条）。到达上限说明数据量异常或 API 行为变化，
# 必须告警而不是静默截断——截断会让「没拉到」被下游误判成「已被移除」。
MAX_CATEGORY_PAGES = 60


def fetch_category_members(
    cmtitle: str,
    headers=None,
    api_url: str = PRTS_API_URL,
    max_pages: int = MAX_CATEGORY_PAGES,
    timeout: int = 30,
) -> list:
    """翻页拉取某个 MediaWiki 分类的全部成员，返回 title 列表（去重、保序）。

    之前三处调用方各自实现，且都只取第一页或写死页数循环：分页没结束就退出时
    结果会被静默截断，下游 diff 会把漏爬的条目当成「PRTS 上已移除」。
    这里统一为：带 continue.cmcontinue 一直翻到没有 continue 为止；命中页数上限
    时打印告警；每次请求先校验状态码，非 200 直接抛错而不是当成空列表。
    """
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": cmtitle,
        "cmlimit": CMLIMIT,
        "format": "json",
    }
    titles = []
    seen = set()
    truncated = False
    for page in range(max_pages):
        resp = requests.get(
            api_url, params=params, headers=headers or PRTS_HEADERS, timeout=timeout
        )
        resp.raise_for_status()  # 5xx/429/403 不能被当成「分类为空」
        data = resp.json()
        for m in data.get("query", {}).get("categorymembers", []):
            title = m.get("title")
            if title and title not in seen:
                seen.add(title)
                titles.append(title)
        cont = data.get("continue")
        if not cont or "cmcontinue" not in cont:
            break
        params["cmcontinue"] = cont["cmcontinue"]
    else:
        truncated = True
        print(
            f"[WARN] 分类 {cmtitle} 达到分页上限（{max_pages} 页 × {CMLIMIT} 条），"
            f"结果可能被截断；已取得 {len(titles)} 条，请调大 MAX_CATEGORY_PAGES 后重跑。",
            file=sys.stderr,
            flush=True,
        )

    if truncated:
        print(
            f"[WARN] {cmtitle} 列表不完整：下游 diff 会把未拉取到的条目误判为「已移除」。",
            file=sys.stderr,
            flush=True,
        )
    return titles
