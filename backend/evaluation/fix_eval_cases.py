"""修复单条评测结果。

用途：整轮评测里若个别用例的回答生成失败（回退成 ground_truth）或返回截断
残句，会让这些用例的 faithfulness / answer_relevancy 失真。本脚本只对这批
用例重跑「生成回答 + 四项指标」，再把结果合并回原结果文件，得到一份干净的
全量基线，避免重跑整轮（105 条 × 4 指标 = 420 次 judge 调用）。

用法
----
    python backend/evaluation/fix_eval_cases.py \
        --result backend/evaluation/results/rag_eval_20260917_203802.csv \
        --out    backend/evaluation/results/rag_eval_20260917_203802_fixed.csv
"""

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.evaluation.rag_eval import (  # noqa: E402
    build_dataset, generate_answer, retrieve_contexts, run_evaluation_with_llm,
)

METRICS = ["context_precision", "context_recall", "faithfulness", "answer_relevancy"]


def pick_bad(rows: list, test_cases: dict) -> list:
    """挑出需要修复的用例：
    1) response == reference  —— 生成失败回退成 ground_truth；
    2) response 长度 < 10 或疑似截断 —— 残句会拉低 faithfulness。
    """
    bad = []
    for r in rows:
        q = (r.get("user_input") or "").strip()
        resp = (r.get("response") or "").strip()
        ref = (r.get("reference") or "").strip()
        if not q or q not in test_cases:
            continue
        reason = None
        if resp and resp == ref:
            reason = "回退为 ground_truth"
        elif len(resp) < 10 or resp.endswith(("是", "为", "的", "：", ":")):
            reason = f"回答过短或截断({len(resp)}字)"
        if reason:
            bad.append((test_cases[q], reason))
    return bad


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.result, encoding="utf-8-sig")))
    test_cases = {c["question"]: c for c in
                  json.load(open(ROOT / "backend/evaluation/test_cases.json", encoding="utf-8"))}

    bad = pick_bad(rows, test_cases)
    print(f"待修复用例: {len(bad)} 条")
    for c, reason in bad:
        print(f"  - [{reason}] {c['question']}")

    if not bad:
        print("无需修复")
        return

    # 重新生成回答（带 rag_eval 里的截断/空响应重试守卫）
    answers = {}
    for c, reason in bad:
        ctx = await retrieve_contexts(c["question"], top_k=5, search_mode="balanced")
        ans = await generate_answer(c["question"], ctx) if ctx else ""
        print(f"  重新生成: {len(ans)} 字 | {c['question']}")
        if ans:
            answers[c["question"]] = ans

    # 只对这批用例重算指标（用预算好的回答，避免二次生成）
    subset = [c for c, _ in bad]
    ds = await build_dataset(subset, top_k=5, search_mode="balanced",
                             with_answer=True, precomputed_answers=answers)
    if ds is None:
        print("数据集构建失败")
        return

    print("重算指标中...")
    result = run_evaluation_with_llm(ds, include_answer_metrics=True)
    rdf = result.to_pandas()
    new_scores = {row["user_input"]: {m: row.get(m) for m in METRICS} for _, row in rdf.iterrows()}

    # 合并
    patched = 0
    for r in rows:
        q = (r.get("user_input") or "").strip()
        if q in new_scores:
            if q in answers:
                r["response"] = answers[q]
            for m in METRICS:
                v = new_scores[q].get(m)
                r[m] = "" if v is None else f"{float(v):.10f}"
            patched += 1

    Path(args.out).write_text("", encoding="utf-8")
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"已修复 {patched} 条 -> {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
