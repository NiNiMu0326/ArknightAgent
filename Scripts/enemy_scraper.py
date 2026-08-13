"""
PRTS Wiki 敌人数据爬虫
======================
从 PRTS Wiki 爬取敌人数据，格式化为 JSON。

敌人 wikitext 结构:
  {{敌人信息/common2
  |id=...
  |名称=...
  |index=...
  ...
  }}

  ==级别0==
  {{敌人信息/levelcontent
  |index=0
  |最大生命值=...
  |攻击力=...
  ...
  }}

使用方式:
  python Scripts/enemy_scraper.py              # 全量爬取
  python Scripts/enemy_scraper.py --incremental  # 增量同步
"""

import requests
import json
import re
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
SCRIPTS_DIR = Path(__file__).parent

sys.path.insert(0, str(Path(__file__).parent))
from common import PRTS_HEADERS

HEADERS = PRTS_HEADERS

# ===================== PRTS API 工具 =====================

def get_enemy_list_from_api() -> list:
    """通过 PRTS API Category 获取敌人列表。"""
    url = "https://prts.wiki/api.php"
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": "Category:敌人",
        "cmlimit": 500,
        "format": "json",
    }
    names = []
    # 可能需要翻页
    for _ in range(10):  # 最多 5000 个敌人
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = resp.json()
        members = data.get("query", {}).get("categorymembers", [])
        for m in members:
            title = m["title"].strip()
            if title.startswith(("Category:", "File:", "模板:")):
                continue
            if any(c in title for c in ("/", "(", ")", ";", "{", "}")):
                continue
            if len(title) < 1 or len(title) > 30:
                continue
            names.append(title)
        # 翻页
        if "continue" in data:
            params["cmcontinue"] = data["continue"]["cmcontinue"]
        else:
            break

    return sorted(names)


def get_enemy_wikitext(name: str) -> "Optional[str]":
    """获取敌人页面的 wikitext。"""
    url = "https://prts.wiki/api.php"
    params = {
        "action": "parse",
        "page": name,
        "prop": "wikitext",
        "format": "json",
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = resp.json()
        return data["parse"]["wikitext"]["*"]
    except Exception as e:
        print(f"  ✗ API 获取失败: {e}")
        return None


# ===================== 解析 =====================

def parse_enemy(name: str) -> "Optional[dict]":
    """解析单个敌人的 wikitext 为结构化 dict。"""
    wikitext = get_enemy_wikitext(name)
    if not wikitext:
        return None

    result = {}

    # 1. 解析 {{敌人信息/common2}} 基本信息块
    common_match = re.search(r'\{\{敌人信息/common2\s*\n(.*?)\n\}\}', wikitext, re.DOTALL)
    if common_match:
        common_block = common_match.group(1)
        fields = {
            "名称": "名称",
            "敌人索引": "index",
            "种类": "种类",
            "地位级别": "地位级别",
            "攻击类型": "攻击方式",
            "伤害类型": "伤害类型",
            "行动方式": "行动方式",
            "描述": "描述",
        }
        for json_key, wiki_field in fields.items():
            match = re.search(rf'\|{wiki_field}=([^\n]+)', common_block)
            if match:
                result[json_key] = match.group(1).strip()

        # 能力：从 common block 提取，也可能在 level 数据中
        ability_match = re.search(r'\|能力=([^\n]+)', common_block)
        if ability_match:
            result["能力"] = ability_match.group(1).strip()

        # 登场活动/关卡
        stage_match = re.search(r'\|登场活动=([^\n]+)', common_block)
        if stage_match:
            result["出场关卡"] = stage_match.group(1).strip()

    # 2. 解析每个级别数据
    levels = []
    level_blocks = re.findall(
        r'==级别(\d+)==\s*\n\{\{敌人信息/levelcontent\s*\n(.*?)\n\}\}',
        wikitext, re.DOTALL
    )

    for level_num, block in level_blocks:
        level_data = {"级别": int(level_num), "属性": {}}

        # 数值字段
        stat_fields = {
            "最大生命值": "最大生命值",
            "攻击力": "攻击力",
            "防御力": "防御力",
            "法术抗性": "法术抗性",
            "移动速度": "移动速度",
            "攻击间隔": "攻击间隔",
            "元素抗性": "元素抗性",
            "重量等级": "重量等级",
            "攻击速度": "攻击速度",
            "生命恢复速度": "生命恢复速度",
            "sp恢复速度": "sp恢复速度",
            "损伤抵抗": "损伤抵抗",
            "基础嘲讽等级": "基础嘲讽等级",
        }
        for json_key, wiki_field in stat_fields.items():
            match = re.search(rf'\|{wiki_field}=([^\n]+)', block)
            if match:
                val = match.group(1).strip()
                try:
                    level_data["属性"][json_key] = float(val) if "." in val else int(val)
                except ValueError:
                    level_data["属性"][json_key] = val

        # 眩晕/沉默等抗性（文本值）
        resist_fields = ["眩晕抗性", "沉默抗性", "沉睡抗性", "冻结抗性",
                         "浮空抗性", "战栗抗性", "恐惧抗性", "麻痹抗性", "诱导抗性"]
        for f in resist_fields:
            match = re.search(rf'\|{f}=([^\n]+)', block)
            if match:
                level_data["属性"][f] = match.group(1).strip()

        # 描述（级别特定的）
        desc_match = re.search(r'\|描述=([^\n]+)', block)
        if desc_match:
            level_data["描述"] = desc_match.group(1).strip()

        # 能力（级别特定的，覆盖 common 的）
        ability_match = re.search(r'\|能力=([^\n]+)', block)
        if ability_match:
            result["能力"] = ability_match.group(1).strip()

        # 出场关卡（部分敌人在 levelcontent 中有登场关卡）
        stage_match = re.search(r'\|登场关卡=([^\n]+)', block)
        if stage_match:
            result["出场关卡"] = stage_match.group(1).strip()

        levels.append(level_data)

    if levels:
        result["级别数据"] = levels

    # 3. 设置默认值
    result.setdefault("名称", name)
    result.setdefault("敌人索引", "")
    result.setdefault("种类", "")
    result.setdefault("地位级别", "")
    result.setdefault("攻击类型", "")
    result.setdefault("伤害类型", "")
    result.setdefault("行动方式", "")
    result.setdefault("描述", "")
    result.setdefault("能力", "")
    result.setdefault("出场关卡", "")

    return result


# ===================== 同步 =====================

def sync_enemies(dry_run: bool = False) -> list:
    """检测新增敌人并增量爬取。"""
    print("=" * 60)
    print("敌人增量同步")

    # 1. PRTS 列表
    print("获取 PRTS 敌人列表...")
    try:
        prts_names = get_enemy_list_from_api()
        prts_set = set(prts_names)
        print(f"  PRTS 当前: {len(prts_names)} 个敌人条目")
    except Exception as e:
        print(f"  ✗ 获取失败: {e}")
        return []

    # 2. 本地数据
    enemies_file = DATA_DIR / "all_enemies.json"
    if not enemies_file.exists():
        print("  ✗ 本地 all_enemies.json 不存在")
        return []

    with open(enemies_file, "r", encoding="utf-8") as f:
        local_enemies = json.load(f)
    local_names = {e.get("名称", "") for e in local_enemies}
    print(f"  本地: {len(local_enemies)} 个敌人")

    # 3. Diff
    new_names = sorted(n for n in prts_set if n not in local_names)
    removed_names = sorted(n for n in local_names if n not in prts_set)

    if removed_names:
        print(f"  ⚠ PRTS 上已移除: {len(removed_names)} 个")
        for n in removed_names[:10]:
            print(f"      - {n}")

    if not new_names:
        print("  无新增敌人 ✓")
        return []

    print(f"  新增: {len(new_names)} 个")
    for n in new_names[:30]:
        print(f"      + {n}")
    if len(new_names) > 30:
        print(f"      ... 还有 {len(new_names) - 30} 个")

    if dry_run:
        print("  (dry-run 模式，跳过爬取)")
        return []

    # 4. 爬取
    print(f"\n开始爬取 {len(new_names)} 个新敌人...")
    new_enemies = []
    fail_list = []

    for i, name in enumerate(new_names):
        progress = f"[{i+1}/{len(new_names)}]"
        try:
            result = parse_enemy(name)
            if result and result.get("级别数据"):
                new_enemies.append(result)
                print(f"  {progress} ✓ {name} ({len(result.get('级别数据', []))} 个级别)")
            else:
                fail_list.append(name)
                print(f"  {progress} ✗ {name} (解析失败或无级别数据)")
        except Exception as e:
            fail_list.append(name)
            print(f"  {progress} ✗ {name}: {e}")

        time.sleep(0.3)

    # 5. 保存
    if new_enemies:
        local_enemies.extend(new_enemies)
        with open(enemies_file, "w", encoding="utf-8") as f:
            json.dump(local_enemies, f, ensure_ascii=False, indent=2)
        print(f"\n  已追加 {len(new_enemies)} 个敌人到 all_enemies.json")

    if fail_list:
        print(f"  失败 {len(fail_list)} 个: {', '.join(fail_list)}")

    return new_enemies


# ===================== 全量爬取 =====================

def crawl_all_enemies():
    """全量爬取所有敌人（首次使用）。"""
    print("获取敌人列表...")
    names = get_enemy_list_from_api()
    print(f"共 {len(names)} 个敌人条目，开始爬取...")

    results = []
    fail_count = 0

    for i, name in enumerate(names):
        print(f"[{i+1}/{len(names)}] {name}...", end=" ")
        result = parse_enemy(name)
        if result and result.get("级别数据"):
            results.append(result)
            print("✓")
        else:
            fail_count += 1
            print("✗")
        time.sleep(0.3)

    with open(DATA_DIR / "all_enemies.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n完成! {len(results)} 个敌人, 失败 {fail_count}")


# ===================== 主入口 =====================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PRTS 敌人爬虫")
    parser.add_argument("--incremental", action="store_true",
                        help="增量同步模式（只爬新敌人）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只检测，不爬取")
    parser.add_argument("--full", action="store_true",
                        help="全量爬取（覆盖已有数据）")
    args = parser.parse_args()

    if args.full:
        crawl_all_enemies()
    elif args.incremental:
        sync_enemies(dry_run=args.dry_run)
    else:
        # 默认：增量 dry-run
        print("默认增量检测模式。用 --incremental 执行实际爬取。")
        print()
        sync_enemies(dry_run=True)
