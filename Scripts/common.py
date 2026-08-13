"""
脚本共享工具
============
PRTS 请求头等公共常量。

2026-08 起 PRTS 的 Tengine WAF 会拦截缺少 Accept / Accept-Language
头的请求（返回 403 Forbidden），所有访问 prts.wiki 的脚本必须使用
PRTS_HEADERS，不要再单独写裸 User-Agent。
"""

PRTS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
