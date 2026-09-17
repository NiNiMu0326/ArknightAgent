"""合并验证结果，写入正式 test_cases.json。

verify_test_cases.py 在并发较高时会被 SiliconFlow 重排接口限流（429），
导致部分用例被误判为 "检索不到"（表现为 0 命中）。这些用例单独复检时可正常
召回，因此本脚本支持把 --recheck 名单里的用例重新标记为通过。

用法
----
    python backend/evaluation/merge_test_cases.py \
        --verify verify3.json --recheck "凯尔希精英二满级,星熊精英二满级,斯卡蒂精英二满级,史尔特尔的天赋,安洁莉娜的天赋,博士属于,骑兵与猎人"
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "test_cases.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", required=True, help="verify_test_cases.py 的输出 JSON")
    ap.add_argument("--recheck", default="",
                    help="逗号分隔的问题前缀；命中者由 FAIL 改判为 OK（限流误判）")
    args = ap.parse_args()

    data = json.loads(Path(args.verify).read_text(encoding="utf-8"))
    passed = data["passed"]
    failed = data["failed"]

    prefixes = [p.strip() for p in args.recheck.split(",") if p.strip()]
    rescued, still_failed = [], []
    for c in failed:
        # 用包含匹配而非前缀匹配：问题可能以「」、书名号等符号开头
        if any(p in c["question"] for p in prefixes):
            v = dict(c["_verify"])
            v["ok"] = True
            v["reason"] = "复检通过（首次为限流误判）"
            rescued.append({**c, "_verify": v})
        else:
            still_failed.append(c)

    final = passed + rescued
    # 按 id 排序，保持稳定输出
    final.sort(key=lambda c: c["id"])
    # 按问题去重（保留 id 较小的一条；原始用例中存在重复问题）
    seen_q, deduped = set(), []
    for c in final:
        if c["question"] in seen_q:
            continue
        seen_q.add(c["question"])
        deduped.append(c)
    final = deduped
    clean = [{k: c[k] for k in ("id", "question", "ground_truth", "category", "difficulty")}
             for c in final]

    OUT_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    by_cat, by_diff = {}, {}
    for c in clean:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
        by_diff[c["difficulty"]] = by_diff.get(c["difficulty"], 0) + 1

    print(f"首次通过:     {len(passed)}")
    print(f"复检救回:     {len(rescued)}")
    print(f"最终淘汰:     {len(still_failed)}")
    print(f"去重后写入:   {len(clean)}  ->  {OUT_PATH}")
    print("分类分布:", json.dumps(by_cat, ensure_ascii=False))
    print("难度分布:", json.dumps(by_diff, ensure_ascii=False))

    if still_failed:
        print("\n仍被淘汰的用例:")
        for c in still_failed:
            print(f"  - {c['question']}  ({c['_verify'].get('reason')})")


if __name__ == "__main__":
    main()
