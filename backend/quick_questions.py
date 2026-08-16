"""
Quick-question template pools: 4 capability categories.

Each refresh returns a fuller batch (8 questions) built from real entity lists:
- operator names fill 「xxx立绘」 art questions
- stage codes fill 「xxx出怪顺序」 / 「xxx材料掉落」 questions
- stories/enemies/aliases fill RAG questions
Fixed templates act as fallback when local data is unavailable.
"""

import random
from typing import Dict, List, Sequence, Set

STRUCTURED_TEMPLATES = [
    {
        "label": "高攻击六星近卫",
        "question": "哪些六星近卫的精二满级攻击力大于800？",
        "type": "structured",
        "category": "structured",
    },
    {
        "label": "领袖敌人血量榜",
        "question": "领袖级敌人中生命值最高的是谁？",
        "type": "structured",
        "category": "structured",
    },
    {
        "label": "六星重装防御榜",
        "question": "六星重装中精二满级防御力最高的是谁？",
        "type": "structured",
        "category": "structured",
    },
]

PRTS_MCP_TEMPLATES = [
    {
        "label": "1-7出怪顺序",
        "question": "1-7关卡的出怪顺序是什么？",
        "type": "stage",
        "category": "prts_mcp",
    },
    {
        "label": "1-7材料掉落",
        "question": "1-7关卡掉落什么材料？",
        "type": "item",
        "category": "prts_mcp",
    },
    {
        "label": "阿米娅立绘",
        "question": "阿米娅有哪些立绘？",
        "type": "artwork",
        "category": "prts_mcp",
    },
]

RAG_FALLBACK_TEMPLATES = [
    {
        "label": "银灰技能",
        "question": "银灰的技能是什么",
        "type": "skill",
        "category": "rag",
    },
    {
        "label": "乌萨斯的孩子们故事",
        "question": "乌萨斯的孩子们的故事内容",
        "type": "story",
        "category": "rag",
    },
]


def pick_template(templates: Sequence[Dict], exclude_labels: Set[str]) -> Dict:
    """Randomly pick a template whose label was not shown in the previous batch."""
    available = [t for t in templates if t["label"] not in exclude_labels]
    pool = available or list(templates)
    return random.choice(pool)


def make_artwork_question(operator_name: str) -> Dict:
    """xxx立绘：xxx 从干员名称列表抽取。"""
    return {
        "label": f"{operator_name}立绘",
        "question": f"{operator_name}有哪些立绘？请展示其中一张。",
        "type": "artwork",
        "category": "prts_mcp",
    }


def make_stage_enemies_question(stage_code: str) -> Dict:
    """xxx出怪顺序：xxx 从关卡代码列表抽取。"""
    return {
        "label": f"{stage_code}出怪顺序",
        "question": f"{stage_code}关卡的出怪顺序是什么？",
        "type": "stage",
        "category": "prts_mcp",
    }


def make_stage_item_question(stage_code: str) -> Dict:
    """xxx材料掉落：xxx 从关卡代码列表抽取。"""
    return {
        "label": f"{stage_code}材料掉落",
        "question": f"{stage_code}关卡掉落什么材料？",
        "type": "item",
        "category": "prts_mcp",
    }


def pick_unique_questions(
    candidates: Sequence,
    make_question,
    exclude_labels: Set[str],
    count: int,
    attempts_per_item: int = 50,
) -> List[Dict]:
    """Build `count` distinct questions by sampling `candidates` via `make_question`."""
    picked: List[Dict] = []
    if not candidates:
        return picked
    for _ in range(count):
        for _ in range(attempts_per_item):
            question = make_question(random.choice(candidates))
            if question["label"] not in exclude_labels:
                exclude_labels.add(question["label"])
                picked.append(question)
                break
        else:
            # 候选都撞上了已有标签时仍返回一个，避免批次缩水
            question = make_question(random.choice(candidates))
            exclude_labels.add(question["label"])
            picked.append(question)
    return picked


def pick_rag_question(
    operator_names: List[str],
    story_names: List[str],
    enemy_names: List[str],
    alias_candidates: List[tuple],
    exclude_labels: Set[str],
) -> Dict:
    """Pick one RAG-capability question, rotating across template kinds."""
    kinds = []
    if operator_names:
        kinds.append((
            "skill", operator_names,
            lambda name: f"{name}技能",
            lambda name: f"{name}的技能是什么",
        ))
    if story_names:
        kinds.append((
            "story", story_names,
            lambda name: f"{name}故事",
            lambda name: f"{name}的故事内容",
        ))
    if enemy_names:
        kinds.append((
            "enemy", enemy_names,
            lambda name: f"{name}敌人",
            lambda name: f"{name}的属性和能力是什么",
        ))
    if alias_candidates:
        kinds.append((
            "alias", alias_candidates,
            lambda pair: f"{pair[0]}别名",
            lambda pair: f"{pair[0]}的其他名称有哪些",
        ))

    random.shuffle(kinds)
    for kind, candidates, label_fn, question_fn in kinds:
        for _ in range(20):
            chosen = random.choice(candidates)
            label = label_fn(chosen)
            if label not in exclude_labels:
                return {
                    "label": label,
                    "question": question_fn(chosen),
                    "type": kind,
                    "category": "rag",
                }

    fallback = random.choice(RAG_FALLBACK_TEMPLATES)
    return dict(fallback)
