"""评测用例可检索性验证。

对 gen_test_cases.py 产出的候选用例，逐条走真实混合检索链路
（FAISS + BM25 -> RRF -> Cross-Encoder），检查 ground_truth 中的关键事实
是否出现在检索结果里。检索不到的用例对 Ragas 评测无意义，会被标记淘汰。

用法
----
    python backend/evaluation/verify_test_cases.py --candidates out.json
    python backend/evaluation/verify_test_cases.py --candidates out.json --limit 6
"""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# 中文数字 -> 阿拉伯数字，用于面板题的关键事实提取
_CN_NUM = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}


def extract_facts(case: dict) -> list:
    """从 ground_truth 中抽取"必须被检索到"的关键事实串。

    返回一组字符串，只要其中之一出现在检索上下文里即视为命中。

    注意：期望串必须取"文档正文里真的会出现的词"，而不是：
      * 知识图谱的标注词（如「所属」）——正文用自然语言表述；
      * 文档标题（如剧情名「自有一派」）——正文未必重复标题。
    因此关系题取实体名 + 成员标志词，剧情题取角色名与地点名。
    """
    q = case["question"]
    gt = case["ground_truth"]
    cat = case.get("category", "")
    facts = []

    # 1. 数字事实：面板数值、生命值、攻击力等
    for m in re.finditer(r"(攻击力|生命上限|最大生命值|防御力|法术抗性)\s*(?:为)?\s*([0-9]+)", gt):
        facts.append(m.group(2))

    # 2. 星级
    for m in re.finditer(r"([0-9]+)\s*星", gt):
        facts.append(m.group(1))

    # 3. 技能/天赋名：书名号或引号包裹
    for m in re.finditer(r"[「『]([^」』]{2,12})[」』]", gt):
        facts.append(m.group(1))

    if cat == "relationship":
        # 关系题：要求检索到「实体名」+「成员/关系标志词」，
        # 后者取文档正文的常见表述，而不是图谱标注词。
        for m in re.finditer(r"^([\u4e00-\u9fff·A-Za-z0-9]{2,12})与([\u4e00-\u9fff·A-Za-z0-9]{2,12})", gt):
            facts.append(m.group(1))
        for kw in ("干员", "领袖", "成员", "加入", "所属", "合作", "对立",
                   "战友", "师徒", "亲属", "负责人", "指挥官"):
            if kw in gt:
                facts.append(kw)
        return _uniq(facts)

    if cat == "story_character":
        # 剧情题：取角色名与地点名，不取故事标题
        for kw in re.findall(r"[\u4e00-\u9fff]{2,4}(?=是|在|与|提|建议|做工|加入)", gt):
            facts.append(kw)
        for kw in ("卡拉顿", "滴水村", "罗德岛", "巴别塔", "阿米娅", "暴行",
                   "苏茜", "格拉尼", "可萝尔", "感染者"):
            if kw in gt:
                facts.append(kw)
        return _uniq(facts)

    # 4. 地位级别 / 攻击类型（敌人题）
    for kw in ("领袖", "精英", "普通", "近战", "远程", "物理", "法术", "地面", "飞行"):
        if kw in gt:
            facts.append(kw)

    return _uniq(facts)


def _uniq(seq) -> list:
    out = []
    for f in seq:
        f = str(f).strip()
        if len(f) < 1:
            continue
        if f not in out:
            out.append(f)
    return out


def normalize(text: str) -> str:
    """去掉空白与常见分隔符，便于宽松子串匹配。"""
    return re.sub(r"[\s，。、·|｜/()（）\[\]【】:：]+", "", text)


async def verify_one(case: dict, sem, retries: int = 4) -> dict:
    from backend.agent.tool_implementations import execute_rag_search

    async with sem:
        result = None
        last_err = None
        for attempt in range(retries):
            t0 = time.time()
            try:
                result = await execute_rag_search({
                    "query": case["question"],
                    "top_k": 5,
                    "search_mode": "balanced",
                    "enable_parent_expansion": True,
                })
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg = str(e).lower()
                # 429 / 限流 / 网络抖动：退避后重试，避免把限流误判成检索失败
                if any(k in msg for k in ("429", "rate", "too many", "timeout", "connection")):
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                break

        if last_err is not None:
            return {**case, "_verify": {"ok": False,
                                        "reason": f"检索异常(已重试{retries}次): {str(last_err)[:80]}"}}

        elapsed = time.time() - t0
        if not isinstance(result, list) or not result:
            return {**case, "_verify": {"ok": False, "reason": "无检索结果", "elapsed": round(elapsed, 1)}}

        blob = normalize(" ".join(str(it.get("content", "")) for it in result if isinstance(it, dict)))
        facts = extract_facts(case)
        if not facts:
            return {**case, "_verify": {"ok": True, "reason": "无关键事实可校验（默认保留）",
                                        "elapsed": round(elapsed, 1), "hits": 0, "facts": 0}}

        hits = [f for f in facts if normalize(f) in blob]
        ratio = len(hits) / len(facts)
        # 判定：至少命中 1 个，且命中率 >= 40%
        ok = len(hits) >= 1 and ratio >= 0.4
        return {**case, "_verify": {
            "ok": ok,
            "reason": f"命中 {len(hits)}/{len(facts)}",
            "ratio": round(ratio, 2),
            "miss": [f for f in facts if f not in hits][:5],
            "elapsed": round(elapsed, 1),
        }}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True, help="候选用例 JSON 路径")
    ap.add_argument("--out", default="", help="验证结果输出路径")
    ap.add_argument("--limit", type=int, default=0, help="只验证前 N 条")
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()

    cases = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[: args.limit]

    print(f"待验证用例: {len(cases)} 条（并发 {args.concurrency}）")
    sem = asyncio.Semaphore(args.concurrency)
    results = []
    t0 = time.time()
    for i, c in enumerate(cases, 1):
        r = await verify_one(c, sem)
        results.append(r)
        v = r["_verify"]
        flag = "OK  " if v.get("ok") else "FAIL"
        print(f"[{i}/{len(cases)}] {flag} {v.get('reason','')} | {c['question'][:34]}"
              + (f" | 漏: {v.get('miss')}" if not v.get("ok") and v.get("miss") else ""))

    passed = [r for r in results if r["_verify"].get("ok")]
    failed = [r for r in results if not r["_verify"].get("ok")]
    print(f"\n通过 {len(passed)} / 淘汰 {len(failed)} | 耗时 {time.time()-t0:.0f}s")

    by_cat = {}
    for r in passed:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    print("通过用例分类分布:", json.dumps(by_cat, ensure_ascii=False))

    if args.out:
        Path(args.out).write_text(
            json.dumps({"passed": passed, "failed": failed}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"已写入: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
