"""从项目数据源批量生成 Ragas 评测用例（test_cases.json）。

设计原则
--------
1. **答案可溯源**：每条 ground_truth 都由 data/ 下的原始数据派生，不手工编写，
   避免"答案本身是错的"污染评测集。
2. **可验证性优先**：优先抽取单一、确定的事实（星级、职业、面板数值、敌人属性），
   这类问题的 ground_truth 与检索上下文易于比对。
3. **难度分层**：easy=单字段查询；medium=需要跨字段/跨文档综合；
   hard=关系推理或需要剧情上下文。
4. **幂等**：重复执行只重建生成区间（id >= KEEP），保留人工维护的原始用例。

用法
----
    python backend/evaluation/gen_test_cases.py            # 生成并覆盖 id>=100 的区间
    python backend/evaluation/gen_test_cases.py --dry-run  # 只打印统计，不写文件
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"
OUT_PATH = Path(__file__).resolve().parent / "test_cases.json"

# 保留人工维护的原始用例（现有 12 条的 id 为 1-12），生成区间从 100 起
GENERATE_ID_START = 100
KEEP_MAX_ID = 12

# ===== 精选干员名单（知名度高、数据完整）=====
# 控制规模：每个干员出 3 题（面板 / 职业 / 技能），15 人 = 45 题
OPERATORS = [
    "银灰", "能天使", "史尔特尔", "艾雅法拉", "陈", "推进之王", "凯尔希", "阿米娅",
    "塞雷娅", "闪灵", "星熊", "安洁莉娜", "伊芙利特", "斯卡蒂", "泥岩",
]

# ===== 精选敌人名单（属性差异明显，便于出题）=====
# 每个敌人出 2 题（属性 / 描述），10 个 = 20 题
ENEMIES = [
    "爱国者", "霜星", "浮士德", "梅菲斯特", "碎骨", "弑君者", "塔露拉",
    "大鲍勃", "庞贝", "皇帝的利刃",
]


def load_json(name: str):
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _fmt_panel(raw: str) -> str:
    """把 '2560 713 397 10' 格式化为可读面板。"""
    parts = str(raw).split()
    if len(parts) != 4:
        return str(raw)
    hp, atk, df, res = parts
    return f"生命上限 {hp}、攻击力 {atk}、防御力 {df}、法术抗性 {res}"


def build_operator_panel_cases(ops) -> list:
    """干员面板数值题：单字段事实，答案确定。"""
    cases = []
    by_name = {o.get("干员名"): o for o in ops}
    for name in OPERATORS:
        o = by_name.get(name)
        if not o:
            continue
        panel = o.get("生命上限_攻击_防御_法术抗性") or {}
        e2 = panel.get("精英2_满级")
        if not e2:
            continue
        cases.append({
            "question": f"{name}精英二满级时的攻击力是多少？",
            "ground_truth": (
                f"{name}精英二满级面板为：{_fmt_panel(e2)}。"
                f"其中攻击力为 {str(e2).split()[1]}。"
            ),
            "category": "operator_info",
            "difficulty": "easy",
        })
    return cases


def build_operator_class_cases(ops) -> list:
    """干员职业/分支/星级题：需要一次属性查询。"""
    cases = []
    by_name = {o.get("干员名"): o for o in ops}
    for name in OPERATORS:
        o = by_name.get(name)
        if not o:
            continue
        star, cls, branch = o.get("星级"), o.get("职业"), o.get("分支")
        if not (star and cls):
            continue
        branch_txt = f"，分支为{branch}" if branch else ""
        cases.append({
            "question": f"{name}是几星干员，职业是什么？",
            "ground_truth": (
                f"{name}是{star}星干员，职业为{cls}{branch_txt}。"
                f"其所属势力为{o.get('所属势力') or '未标注'}，画师为{o.get('画师') or '未标注'}。"
            ),
            "category": "operator_info",
            "difficulty": "easy",
        })
    return cases


def build_operator_skill_cases(ops) -> list:
    """干员技能题：跨字段综合，medium。"""
    cases = []
    by_name = {o.get("干员名"): o for o in ops}
    for name in OPERATORS:
        o = by_name.get(name)
        if not o:
            continue
        skills = o.get("技能") or []
        names = [s.get("名称") for s in skills if isinstance(s, dict) and s.get("名称")]
        if not names:
            continue
        cases.append({
            "question": f"{name}有哪些技能？",
            "ground_truth": (
                f"{name}拥有 {len(names)} 个技能，依次为：{'、'.join(names)}。"
            ),
            "category": "operator_info",
            "difficulty": "medium",
        })
    return cases


def build_operator_talent_cases(ops) -> list:
    """干员天赋题：需要定位到天赋段落，medium。"""
    cases = []
    by_name = {o.get("干员名"): o for o in ops}
    for name in OPERATORS:
        o = by_name.get(name)
        if not o:
            continue
        talents = o.get("天赋") or []
        tnames = [t.get("名称") for t in talents if isinstance(t, dict) and t.get("名称")]
        if not tnames:
            continue
        first = next((t for t in talents if isinstance(t, dict) and t.get("名称")), None)
        e2 = (first or {}).get("精英2") or ""
        cases.append({
            "question": f"{name}的天赋是什么？",
            "ground_truth": (
                f"{name}的天赋为：{'、'.join(tnames)}。"
                + (f"其中「{first.get('名称')}」在精英二阶段的效果为：{e2}" if e2 else "")
            ),
            "category": "operator_info",
            "difficulty": "medium",
        })
    return cases


def _fmt_ability(raw) -> str:
    """把敌人能力文本整理成可读形式（原文用 · 分隔但缺空白）。"""
    if not raw:
        return ""
    txt = str(raw).replace("·", "；").replace("；；", "；").strip("；")
    return re.sub(r"\s+", " ", txt)


def build_enemy_cases(ens) -> list:
    """敌人属性题：等级/攻击类型/生命值，答案确定。"""
    cases = []
    by_name = {}
    for e in ens:
        by_name.setdefault(e.get("名称"), e)
    for name in ENEMIES:
        e = by_name.get(name)
        if not e:
            continue
        ld = e.get("级别数据") or []
        attrs = (ld[0].get("属性") if ld and isinstance(ld[0], dict) else None) or {}
        hp = attrs.get("最大生命值")
        atk = attrs.get("攻击力")
        df = attrs.get("防御力")
        res = attrs.get("法术抗性")
        if hp is None:
            continue
        cases.append({
            "question": f"{name}这个敌人的地位级别和攻击类型是什么？生命值是多少？",
            "ground_truth": (
                f"{name}的地位级别为{e.get('地位级别')}，攻击类型为{e.get('攻击类型')}，"
                f"行动方式为{e.get('行动方式')}。其基础属性为：最大生命值 {hp}、攻击力 {atk}、"
                f"防御力 {df}、法术抗性 {res}。"
                + (f"能力：{_fmt_ability(e.get('能力'))[:180]}" if e.get("能力") else "")
            ),
            "category": "enemy_info",
            "difficulty": "medium",
        })
    return cases


def build_enemy_desc_cases(ens) -> list:
    """敌人背景描述题：需要检索描述文本，medium。"""
    cases = []
    by_name = {}
    for e in ens:
        by_name.setdefault(e.get("名称"), e)
    for name in ENEMIES:
        e = by_name.get(name)
        if not e:
            continue
        desc = (e.get("描述") or "").strip()
        if len(desc) < 20:
            continue
        cases.append({
            "question": f"{name}是什么样的敌人？",
            "ground_truth": (
                f"{name}是{e.get('种类')}类、{e.get('地位级别')}级别的敌人，"
                f"攻击类型为{e.get('攻击类型')}。{desc}"
            ),
            "category": "enemy_info",
            "difficulty": "easy",
        })
    return cases


def build_relationship_cases(kg) -> list:
    """关系推理题：从知识图谱的实体关系派生。

    注意：问法要设计成"答案中会出现关系标注词"的形式。早期版本问
    "A和B是什么关系"，而文档正文用自然语言表述（"X是罗德岛的先锋干员"），
    不会出现图谱里的标注词「所属」，导致校验误判。改用"X属于哪个组织"后，
    「所属」可以作为答案词被命中。
    """
    cases = []
    rels = kg.get("relations") or []
    # (关系类型, 问法模板, 难度)
    templates = {
        "所属": ("{src}属于哪个组织或势力？", "medium"),
        "对立": ("{src}与{tgt}是对立关系吗？", "hard"),
        "合作": ("{src}和{tgt}之间是什么合作关系？", "medium"),
        "亲属": ("{src}和{tgt}是什么亲属关系？", "hard"),
        "战友": ("{src}和{tgt}是战友吗？", "medium"),
        "师徒": ("{src}和{tgt}是师徒关系吗？", "medium"),
    }
    seen = set()
    for r in rels:
        src, tgt, rel = r.get("source"), r.get("target"), r.get("relation")
        desc = (r.get("description") or "").strip()
        if not (src and tgt and rel) or rel not in templates:
            continue
        if len(desc) < 6:
            continue
        key = (src, tgt, rel)
        if key in seen:
            continue
        seen.add(key)
        q_tpl, diff = templates[rel]
        cases.append({
            "question": q_tpl.format(src=src, tgt=tgt),
            "ground_truth": (
                f"{src}与{tgt}属于「{rel}」关系。{desc}"
            ),
            "category": "relationship",
            "difficulty": diff,
        })
    return cases[:12]


def build_gameplay_cases() -> list:
    """玩法机制题。

    已停用：gameplay.md 是表格型文档，按标题抽正文会生成
    "明日方舟的1. 主题曲是什么机制？" 这类噪声题，且 knowledge 集合中
    gameplay 仅 13 个 chunk，评估价值低于噪声成本。保留函数以便将来
    用人工整理的种子问题替换。
    """
    return []


# ===== 人工维护的补充用例 =====
# 从已确认可检索的文档内容（干员档案 / 剧情总结）人工撰写，覆盖
# 关系推理与剧情理解两类，弥补自动生成在这两个类别上的不足。
CURATED_CASES = [
    {
        "question": "阿米娅在罗德岛担任什么职务？",
        "ground_truth": (
            "阿米娅是罗德岛的公开领袖。她出身于雷姆必拓，是卡特斯/奇美拉感染者，"
            "自幼在巴别塔长大，是特蕾西娅临终前托付「文明的存续」（黑王冠）的继承者，"
            "成为萨卡兹的新一任「魔王」。博士失忆苏醒后，阿米娅作为罗德岛的核心领导，"
            "带领组织在泰拉大陆各方势力间斡旋，致力于治愈矿石病、弥合感染者与普通人之间的仇恨。"
        ),
        "category": "relationship",
        "difficulty": "easy",
    },
    {
        "question": "斯卡蒂为什么加入罗德岛？",
        "ground_truth": (
            "斯卡蒂是来自阿戈尔的深海猎人，在陆地上以赏金猎人身份行动。"
            "她曾长期自我孤立，认为自己的存在会给身边的人带来灾祸；"
            "加入罗德岛后逐渐与其他干员建立联系，并在寻找昔日战友幽灵鲨的过程中，"
            "揭示了深海猎人与海嗣之间不为人知的联系。"
        ),
        "category": "relationship",
        "difficulty": "hard",
    },
    {
        "question": "格拉尼在罗德岛是什么身份？",
        "ground_truth": (
            "格拉尼是罗德岛的先锋干员。她出身维多利亚，是库兰塔族女性，曾是一名正直热情的骑警；"
            "因不愿向腐败和不公低头而失去原本身份，但始终坚持信念，以手中长枪守护弱者，"
            "凭借出色的机动性和坚韧意志活跃在各种任务中。"
        ),
        "category": "relationship",
        "difficulty": "medium",
    },
    {
        "question": "歌蕾蒂娅与罗德岛是什么关系？",
        "ground_truth": (
            "歌蕾蒂娅现与罗德岛建立合作关系，担任罗德岛的阿戈尔事务负责人，"
            "同时领导幸存的深海猎人。她来自深海文明阿戈尔，曾任阿戈尔技术执政官"
            "和深海猎人总战争设计师之一。"
        ),
        "category": "relationship",
        "difficulty": "hard",
    },
    {
        "question": "暴行和罗德岛有什么关系？",
        "ground_truth": (
            "暴行本名夏洛特，来自雷姆必拓的卡特斯族女性干员，在罗德岛担任近卫干员。"
            "巴别塔时期她曾作为旅伴陪伴博士和阿米娅，与阿米娅建立深厚友谊；"
            "回到雷姆必拓后因家乡矿难再次接触罗德岛，得知阿米娅是罗德岛领袖后，"
            "怀着保护朋友和守护家乡的决心加入罗德岛。"
        ),
        "category": "relationship",
        "difficulty": "medium",
    },
    {
        "question": "「骑兵与猎人」这段剧情讲了什么？",
        "ground_truth": (
            "故事始于卡西米尔一处偏远村庄滴水村，因流传「骑士宝藏」传说吸引大量赏金猎人，"
            "村庄被搅得不得安宁。村民的委托信最终落到罗德岛干员、前骑警格拉尼手中。"
            "格拉尼抵达时村子已被赏金猎人破坏，村长可萝尔被严刑拷打追问宝藏下落，"
            "格拉尼及时出手击退赏金猎人救下可萝尔。"
        ),
        "category": "story_character",
        "difficulty": "hard",
    },
    {
        "question": "「自有一派」这段剧情的核心内容是什么？",
        "ground_truth": (
            "剧情围绕雷姆必拓矿业公司驻罗德岛办事处的干员暴行，以及她与阿米娅、博士等人的互动展开。"
            "故事始于阿米娅因博士、凯尔希和特蕾西娅外出而感到无聊，前来寻找暴行，"
            "暴行提议教阿米娅制作雷姆必拓特色美食胡萝卜派，两人在温馨氛围中准备食材。"
        ),
        "category": "story_character",
        "difficulty": "medium",
    },
    {
        "question": "「阴云火花」这段剧情发生在哪里，讲了什么？",
        "ground_truth": (
            "故事主要围绕卡拉顿这座维多利亚城市的感染者社区展开，通过几条并行线索"
            "揭示底层人们的困境、少数理想主义者的努力以及城市光鲜之下的腐败与阴谋。"
            "故事始于感染者少女苏茜在「绿意火花」小店做工的生活——这家店是她在感染者社区中"
            "难得的温暖港湾，也是她多年攒钱希望买下的梦想之地。"
        ),
        "category": "story_character",
        "difficulty": "hard",
    },
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="", help="输出路径（默认覆盖 test_cases.json）")
    args = ap.parse_args()

    ops = load_json("all_operators.json")
    ens = load_json("all_enemies.json")
    with open(ROOT / "chunks" / "graphrag" / "entity_relations.json", encoding="utf-8") as f:
        kg = json.load(f)

    existing = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    kept = [c for c in existing if c.get("id", 0) <= KEEP_MAX_ID]

    generated = []
    generated += build_operator_panel_cases(ops)
    generated += build_operator_class_cases(ops)
    generated += build_operator_skill_cases(ops)
    generated += build_operator_talent_cases(ops)
    generated += build_enemy_cases(ens)
    generated += build_enemy_desc_cases(ens)
    generated += build_relationship_cases(kg)
    generated += build_gameplay_cases()
    generated += [dict(c) for c in CURATED_CASES]

    # 去重（同一 question 只保留一条）
    seen_q = {c["question"] for c in kept}
    deduped = []
    for c in generated:
        if c["question"] in seen_q:
            continue
        seen_q.add(c["question"])
        deduped.append(c)

    next_id = GENERATE_ID_START
    for c in deduped:
        c["id"] = next_id
        next_id += 1

    # 字段顺序统一为 id/question/ground_truth/category/difficulty
    ordered = [
        {k: c[k] for k in ("id", "question", "ground_truth", "category", "difficulty")}
        for c in kept + deduped
    ]

    print(f"保留原有用例: {len(kept)}")
    print(f"新生成用例:   {len(deduped)}  （去重后）")
    print(f"合计:         {len(ordered)}")
    by_cat = {}
    for c in ordered:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    print("分类分布:", json.dumps(by_cat, ensure_ascii=False))
    by_diff = {}
    for c in ordered:
        by_diff[c["difficulty"]] = by_diff.get(c["difficulty"], 0) + 1
    print("难度分布:", json.dumps(by_diff, ensure_ascii=False))

    if args.dry_run:
        print("\n[dry-run] 未写入文件")
        return

    if len(ordered) < 50:
        print(f"\n警告: 仅 {len(ordered)} 条，少于目标 50 条", file=sys.stderr)

    out_path = Path(args.out) if args.out else OUT_PATH
    out_path.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n已写入: {out_path}")


if __name__ == "__main__":
    main()
