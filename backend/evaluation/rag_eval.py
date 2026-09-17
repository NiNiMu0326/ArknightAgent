"""
RAG 检索质量评估脚本。
使用 RAGAS 框架评估 arknights_rag_search 工具的检索效果。

评估指标 (RAGAS 0.4.x, 使用旧版 ragas.metrics 接口兼容 evaluate()):
- ContextRelevance:      检索结果与问题的相关性 (只需 question + contexts)
- LLMContextRecall:      检索结果对 ground_truth 的覆盖率 (需要 reference)
- Faithfulness:          回答是否忠于检索到的文档 (需要 question + contexts + response)
- AnswerRelevancy:       回答与问题的相关程度 (需要 question + response)

用法:
    python backend/evaluation/rag_eval.py                       # LLM评估模式 (推荐)
    python backend/evaluation/rag_eval.py --no-llm              # NonLLM模式 (字符串匹配)
    python backend/evaluation/rag_eval.py --top-k 10            # 设置检索返回数量
    python backend/evaluation/rag_eval.py --output-dir results  # 设置输出目录
    python backend/evaluation/rag_eval.py --with-answer         # 同时生成回答并评估 faithfulness/relevancy
"""

import asyncio
import json
import sys
import os
import argparse
import logging
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from backend import config

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import nest_asyncio
nest_asyncio.apply()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ===== Judge LLM 模型配置 (按优先级排列，限速时自动切换) =====
# API Key 从环境变量读取，不要硬编码到代码里！
# 在 backend/.env 中配置 RAGAS_DEEPSEEK_API_KEY / RAGAS_MIMO_API_KEY
JUDGE_MODELS = [
    {
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "api_key": os.environ.get("RAGAS_DEEPSEEK_API_KEY", ""),
        "name": "DeepSeek-v4-flash (primary)",
    },
    {
        "model": "mimo-v2.5-pro",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "api_key": os.environ.get("RAGAS_MIMO_API_KEY", ""),
        "name": "MiMo-v2.5-pro (backup)",
    },
]
JUDGE_MODELS = [m for m in JUDGE_MODELS if m["api_key"]]
if not JUDGE_MODELS:
    logger.warning("未配置任何 judge 模型 API Key (RAGAS_DEEPSEEK_API_KEY / RAGAS_MIMO_API_KEY)")


async def generate_answer(question: str, contexts: List[str], model: str = "deepseek-v4-flash") -> str:
    """Generate an answer using LLM based on retrieved contexts.

    This simulates the Agent final answer step (after tool retrieval),
    so RAGAS can evaluate faithfulness and answer_relevancy.
    """
    import httpx
    from backend import config

    if not contexts:
        return ""

    context_block = "\n\n".join([f"[\u6587\u6863{i+1}] {c}" for i, c in enumerate(contexts)])
    prompt = f"""\u57fa\u4e8e\u4ee5\u4e0b\u68c0\u7d22\u5230\u7684\u6587\u6863\u56de\u7b54\u7528\u6237\u95ee\u9898\u3002
\u8981\u6c42\uff1a
- \u53ea\u4f7f\u7528\u6587\u6863\u4e2d\u7684\u4fe1\u606f\u56de\u7b54\uff0c\u4e0d\u8981\u7f16\u9020
- \u5982\u679c\u6587\u6863\u4e2d\u6ca1\u6709\u76f8\u5173\u4fe1\u606f\uff0c\u5982\u5b9e\u8bf4\u660e
- \u56de\u7b54\u8981\u51c6\u786e\u3001\u7b80\u6d01

## \u68c0\u7d22\u5230\u7684\u6587\u6863
{context_block}

## \u7528\u6237\u95ee\u9898
{question}

\u8bf7\u56de\u7b54\uff1a"""

    api_key = config.DEEPSEEK_API_KEY
    if not api_key:
        logger.warning("DEEPSEEK_API_KEY not set, skipping answer generation")
        return ""

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        # 注意：deepseek-v4-flash 是思考模型，max_tokens 是「思考 + 可见回答」的
        # 总预算。若设得过小（如 1024），复杂问题的 reasoning 会吃光全部预算，
        # 导致 finish_reason=length 且 content 为空字符串。因此给足余量。
        "max_tokens": 8192,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # 重试：评测跑上百条时，DeepSeek 偶发超时/限流/空响应。若直接返回空串，
    # build_dataset 会静默回退成 ground_truth 当回答，导致 faithfulness /
    # answer_relevancy 被虚高（回答与参考答案完全一致）。因此这里必须重试。
    last_err = None
    for attempt in range(4):
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{config.DEEPSEEK_BASE_URL}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                content = (choice["message"].get("content") or "").strip()
                finish = choice.get("finish_reason")
                # finish_reason=length 说明思考过程吃光了 max_tokens 预算，
                # 此时 content 往往为空——必须重试而不是当作"回答了"。
                if finish == "length":
                    last_err = f"输出被截断(finish_reason=length), 可见回答 {len(content)} 字"
                # 截断检测：偶发会返回极短的残句（实测出现过仅 4 字的
                # "大鲍勃是"），这种回答会被判 faithfulness=0，必须重试。
                elif len(content) >= 10 and not content.endswith(("是", "为", "的", "：", ":", "，", ",")):
                    return content
                else:
                    last_err = f"内容过短或疑似截断: {content[:30]!r}"
        except Exception as e:  # noqa: BLE001
            last_err = e
        await asyncio.sleep(2 * (attempt + 1))

    logger.error(f"Answer generation failed after retries: {last_err}")
    return ""


async def retrieve_contexts(question: str, top_k: int = 5, search_mode: str = "balanced", enable_parent_expansion: bool = True) -> List[str]:
    """\u8c03\u7528 RAG \u68c0\u7d22\u83b7\u53d6\u4e0a\u4e0b\u6587\u6587\u672c\u5217\u8868\u3002"""
    from backend.agent.tool_implementations import execute_rag_search

    result = await execute_rag_search({"query": question, "top_k": top_k, "search_mode": search_mode, "enable_parent_expansion": enable_parent_expansion})

    contexts = []
    for item in result:
        if isinstance(item, dict) and "content" in item and "error" not in item:
            contexts.append(item["content"])
    return contexts


def run_evaluation_no_llm(dataset):
    """使用 LLM 指标评估 (使用 MiMo 作为 judge)。

    只测 ContextRelevance + ContextRecall (不需要 response)。
    """
    return run_evaluation_with_llm(dataset, include_answer_metrics=False)


def run_evaluation_with_llm(dataset, include_answer_metrics: bool = False):
    """使用 LLM 指标评估，支持多 judge 模型自动 failover。"""
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    from ragas import evaluate
    from ragas.metrics._context_recall import context_recall
    from ragas.metrics._context_precision import context_precision
    from langchain_openai import ChatOpenAI
    from ragas.llms import LangchainLLMWrapper

    metrics = [context_precision, context_recall]

    if include_answer_metrics:
        from ragas.metrics._faithfulness import faithfulness
        from ragas.metrics._answer_relevance import answer_relevancy
        # answer_relevancy needs embeddings; use SiliconFlow bge-m3
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from langchain_openai import OpenAIEmbeddings
        from backend import config as _cfg
        sf_embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(
            model="Pro/BAAI/bge-m3",
            openai_api_key=_cfg.SILICONFLOW_API_KEY,
            openai_api_base=_cfg.SILICONFLOW_BASE_URL,
        ))
        answer_relevancy.embeddings = sf_embeddings

        metrics.append(faithfulness)
        metrics.append(answer_relevancy)

    # 尝试每个 judge 模型，限速(429)或失败时自动切换到下一个
    last_error = None
    for model_cfg in JUDGE_MODELS:
        try:
            judge_llm = ChatOpenAI(
                model=model_cfg["model"],
                base_url=model_cfg["base_url"],
                api_key=model_cfg["api_key"],
                temperature=0,
                timeout=120,
                max_tokens=8192,
            )
            wrapped_llm = LangchainLLMWrapper(judge_llm, bypass_n=True)

            for m in metrics:
                if hasattr(m, 'llm'):
                    m.llm = wrapped_llm

            logger.info(f"使用 LLM 指标评估 ({model_cfg['name']} 作为 judge)...")
            from ragas.run_config import RunConfig
            run_config = RunConfig(max_workers=4, max_retries=3, max_wait=30, timeout=120)
            logger.info(f"RunConfig: max_workers={run_config.max_workers}, max_retries={run_config.max_retries}")
            result = evaluate(dataset=dataset, metrics=metrics, run_config=run_config)
            return result
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "rate" in err_str or "limit" in err_str or "quota" in err_str:
                logger.warning(f"Judge {model_cfg['name']} 被限速，切换到下一个模型...")
                last_error = e
                continue
            else:
                raise

    raise RuntimeError(f"所有 judge 模型均失败，最后一个错误: {last_error}")



async def build_dataset(test_cases: List[Dict], top_k: int = 5, search_mode: str = "balanced",
                        use_reference_contexts: bool = False,
                        with_answer: bool = False,
                        enable_parent_expansion: bool = True,
                        precomputed_answers: Optional[Dict[str, str]] = None):
    """\u6784\u5efa RAGAS \u8bc4\u4f30\u6570\u636e\u96c6\u3002

    RAGAS 0.4.x \u65e7\u7248\u63a5\u53e3\u5217\u540d:
    - user_input: \u95ee\u9898
    - retrieved_contexts: \u68c0\u7d22\u5230\u7684\u4e0a\u4e0b\u6587
    - reference: \u6807\u51c6\u7b54\u6848 (ground_truth)
    - reference_contexts: \u6807\u51c6\u4e0a\u4e0b\u6587 (NonLLM \u6a21\u5f0f\u4f7f\u7528)
    - response: LLM \u751f\u6210\u7684\u56de\u7b54 (faithfulness/answer_relevancy \u4f7f\u7528)
    """
    from datasets import Dataset

    questions = []
    contexts_list = []
    references = []
    reference_contexts_list = []
    responses = []
    categories = []
    skipped = 0
    fallback_cases: List[str] = []

    for i, tc in enumerate(test_cases):
        q = tc["question"]
        gt = tc["ground_truth"]
        cat = tc.get("category", "unknown")

        logger.info(f"[{i+1}/{len(test_cases)}] \u68c0\u7d22: {q}")
        t0 = time.time()
        contexts = await retrieve_contexts(q, top_k=top_k, search_mode=search_mode)
        elapsed = time.time() - t0
        logger.info(f"  -> \u8fd4\u56de {len(contexts)} \u4e2a\u7ed3\u679c ({elapsed:.1f}s)")

        if not contexts:
            logger.warning(f"  -> \u65e0\u7ed3\u679c\uff0c\u8df3\u8fc7")
            skipped += 1
            continue

        questions.append(q)
        contexts_list.append(contexts)
        references.append(gt)
        categories.append(cat)

        if use_reference_contexts:
            reference_contexts_list.append([gt])

        # Generate answer if needed
        if with_answer:
            logger.info(f"  -> \u751f\u6210\u56de\u7b54...")
            answer = (precomputed_answers or {}).get(q) or await generate_answer(q, contexts)
            if answer:
                responses.append(answer)
                logger.info(f"  -> \u56de\u7b54\u957f\u5ea6: {len(answer)} \u5b57")
            else:
                # 回退会把 ground_truth 当作回答，使 faithfulness /
                # answer_relevancy 虚高（回答与参考答案完全一致）。
                # 因此显式记录，并在结果里提示这些指标不可信。
                responses.append(gt)
                fallback_cases.append(q)
                logger.warning(f"  -> \u56de\u7b54\u751f\u6210\u5931\u8d25\uff0c\u4f7f\u7528 ground_truth \u66ff\u4ee3")

    if fallback_cases:
        logger.warning(
            f"[WARN] {len(fallback_cases)} \u6761\u7528\u4f8b\u56de\u9000\u4e3a ground_truth \u4f5c\u7b54\uff0c"
            f"faithfulness / answer_relevancy \u4f1a\u88ab\u865a\u9ad8\uff0c\u8bf7\u91cd\u8dd1\u6216\u5254\u9664\u540e\u518d\u770b\u8fd9\u4e24\u9879"
        )
        for q in fallback_cases:
            logger.warning(f"    - {q}")

    if not questions:
        logger.error("\u6240\u6709\u6d4b\u8bd5\u7528\u4f8b\u5747\u65e0\u68c0\u7d22\u7ed3\u679c, \u65e0\u6cd5\u8bc4\u4f30")
        return None

    data_dict = {
        "user_input": questions,
        "retrieved_contexts": contexts_list,
        "reference": references,
        "category": categories,
    }
    if use_reference_contexts:
        data_dict["reference_contexts"] = reference_contexts_list
    if with_answer:
        data_dict["response"] = responses

    dataset = Dataset.from_dict(data_dict)

    logger.info(f"\u6570\u636e\u96c6\u6784\u5efa\u5b8c\u6210: {len(questions)} \u6761 (\u8df3\u8fc7 {skipped} \u6761)")
    return dataset


def print_results(result_df, metrics_names):
    """\u6253\u5370\u8bc4\u4f30\u7ed3\u679c\u3002"""
    print()
    print("=" * 60)
    print("  RAG \u68c0\u7d22\u8d28\u91cf\u8bc4\u4f30\u62a5\u544a")
    print("=" * 60)

    print()
    print("\u603b\u4f53\u6307\u6807:")
    print("-" * 40)
    for col in result_df.columns:
        if col in metrics_names:
            mean_val = result_df[col].mean()
            min_val = result_df[col].min()
            max_val = result_df[col].max()
            print(f"  {col:30s}  mean={mean_val:.3f}  min={min_val:.3f}  max={max_val:.3f}")

    print()
    print("\u9010\u9898\u8be6\u60c5:")
    print("-" * 40)
    for i, row in result_df.iterrows():
        q = row.get("user_input", f"Q{i+1}")
        q_short = q[:40] + "..." if len(q) > 40 else q
        print(f"  [{i+1}] {q_short}")
        for col in result_df.columns:
            if col in metrics_names:
                val = row[col]
                if val is not None and str(val) != "nan":
                    print(f"      {col}: {val:.3f}")
        print()

    if "category" in result_df.columns:
        print("\u6309\u7c7b\u522b\u6c47\u603b:")
        print("-" * 40)
        for cat in result_df["category"].unique():
            cat_df = result_df[result_df["category"] == cat]
            print(f"  [{cat}] ({len(cat_df)} items)")
            for col in result_df.columns:
                if col in metrics_names:
                    mean_val = cat_df[col].mean()
                    if str(mean_val) != "nan":
                        print(f"    {col}: {mean_val:.3f}")
            print()

    print("=" * 60)


def save_results(result_df, output_dir: str = "backend/evaluation/results"):
    """\u4fdd\u5b58\u8bc4\u4f30\u7ed3\u679c\u3002"""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    csv_path = os.path.join(output_dir, f"rag_eval_{timestamp}.csv")
    json_path = os.path.join(output_dir, f"rag_eval_{timestamp}.json")

    result_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    result_df.to_json(json_path, orient="records", force_ascii=False, indent=2)

    logger.info(f"\u7ed3\u679c\u5df2\u4fdd\u5b58: {csv_path}")
    logger.info(f"\u7ed3\u679c\u5df2\u4fdd\u5b58: {json_path}")
    return csv_path, json_path


def append_history(result_df, metrics_names: list, tag: str = "",
                   output_dir: str = "backend/evaluation/results",
                   extra_info: dict = None):
    """追加一行评测记录到 eval_history.jsonl。"""
    os.makedirs(output_dir, exist_ok=True)
    history_path = os.path.join(output_dir, "eval_history.jsonl")

    record = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tag": tag,
        "num_cases": len(result_df),
    }
    for col in metrics_names:
        if col in result_df.columns:
            vals = result_df[col].dropna()
            record[col] = round(float(vals.mean()), 4) if len(vals) > 0 else None
    if extra_info:
        record.update(extra_info)

    with open(history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info(f"评测记录已追加: {history_path}  tag={tag}")
    return history_path


async def main():
    parser = argparse.ArgumentParser(description="RAG \u68c0\u7d22\u8d28\u91cf\u8bc4\u4f30")
    parser.add_argument("--test-file", default="backend/evaluation/test_cases.json",
                        help="\u6d4b\u8bd5\u7528\u4f8b JSON \u6587\u4ef6\u8def\u5f84")
    parser.add_argument("--top-k", type=int, default=5, help="RAG \u68c0\u7d22\u8fd4\u56de\u7684\u7ed3\u679c\u6570")
    parser.add_argument("--no-llm", action="store_true",
                        help="\u4f7f\u7528 NonLLM \u6307\u6807 (\u66f4\u5feb\u4f46\u7cbe\u5ea6\u8f83\u4f4e)")
    parser.add_argument("--with-answer", action="store_true",
                        help="\u751f\u6210\u56de\u7b54\u5e76\u8bc4\u4f30 faithfulness/answer_relevancy")
    parser.add_argument("--no-parent-expansion", action="store_true",
                        help="?? Parent Document ??????????")
    parser.add_argument("--search-mode", default="balanced", choices=["precise", "semantic", "balanced"], help="search_mode for RAG retrieval")
    parser.add_argument("--tag", default="", help="本轮评测标签，写入 eval_history.jsonl")
    parser.add_argument("--output-dir", default="backend/evaluation/results",
                        help="\u7ed3\u679c\u8f93\u51fa\u76ee\u5f55")
    args = parser.parse_args()

    logger.info(f"\u52a0\u8f7d\u6d4b\u8bd5\u7528\u4f8b: {args.test_file}")
    with open(args.test_file, "r", encoding="utf-8") as f:
        test_cases = json.load(f)
    logger.info(f"\u5171 {len(test_cases)} \u6761\u6d4b\u8bd5\u7528\u4f8b")

    dataset = await build_dataset(
        test_cases, top_k=args.top_k, search_mode=args.search_mode,
        use_reference_contexts=args.no_llm,
        with_answer=args.with_answer,
        enable_parent_expansion=not args.no_parent_expansion,
    )
    if dataset is None:
        sys.exit(1)

    if args.no_llm:
        result = run_evaluation_no_llm(dataset)
        metrics_names = [
            "context_precision",
            "context_recall",
        ]
    else:
        result = run_evaluation_with_llm(dataset, include_answer_metrics=args.with_answer)
        metrics_names = [
            "context_precision",
            "context_recall",
        ]
        if args.with_answer:
            metrics_names.extend(["faithfulness", "answer_relevancy"])

    result_df = result.to_pandas()
    print_results(result_df, metrics_names)

    csv_path, json_path = save_results(result_df, args.output_dir)

    extra = {
        "mode": "no-llm" if args.no_llm else ("with-answer" if args.with_answer else "llm"),
        "search_mode": args.search_mode,
        "top_k": args.top_k,
        "csv_file": os.path.basename(csv_path),
    }
    append_history(result_df, metrics_names, tag=args.tag, output_dir=args.output_dir, extra_info=extra)


if __name__ == "__main__":
    asyncio.run(main())
