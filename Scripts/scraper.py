"""
PRTS明日方舟Wiki干员数据爬虫
用于从PRTS Wiki爬取干员信息并格式化为JSON

功能说明:
    - 从PRTS Wiki API获取干员 wikitext 和 HTML 内容
    - 解析干员基本信息、属性、潜能加成
    - 解析天赋信息，支持潜能提升效果显示
    - 解析技能信息（Level 7和Rank Ⅲ两个级别）
    - 解析后勤技能和模组信息
    - 支持批量爬取全部干员
    - 从Wiki直接获取画师、配音、所属势力等信息
    - 完全独立运行，不依赖本地all_operators.json

输出文件:
    - all_operators_new.json: 爬取的全部干员数据
    - crawl_log.txt: 爬取日志

使用方式:
    1. 爬取全部干员(需准备干员名列表):
        python scraper.py
        # 修改 test_operators 列表为要爬取的干员名

    2. 爬取指定干员列表:
        # 在 if __name__ == "__main__": 部分修改
        test_operators = ['能天使', '煌', '银灰']

    输出JSON格式:
        {
            "干员名": "煌",
            "星级": "6",
            "职业": "近卫",
            "分支": "术战者 可以且优先攻击自身阻挡的单位...",
            "特性": "攻击造成法术伤害",
            "所属势力": "维多利亚",
            "隐藏势力": "无",
            "获得方式": "公开招募、中坚寻访",
            "上线时间": "2019年4月30日 10:00",
            "画师": "悠哉YOZA",
            "配音": {"中文": "某某", "日文": "某某", "韩文": "某某", "英文": "某某"},
            "生命上限_攻击_防御_法术抗性": {
                "精英0_1级": "1649 429 224 0",
                "精英0_满级": "...",
                "精英1_满级": "...",
                "精英2_满级": "..."
            },
            "信赖加成上限_生命上限_攻击_防御_法术抗性": "0 60 45 0",
            "再部署": "70s",
            "部署费用": "18→21",
            "阻挡数": "3",
            "攻击速度": "1.2s",
            "技能": [
                {"名称": "紧急除颤", "类型": "攻击回复 自动触发", "Level 7_初始_消耗_持续": "0 5 8", "Rank Ⅲ": "...", "备注": null},
                ...
            ],
            "天赋": [
                {"名称": "紧急除颤", "精英2": "...", "精英2 Y模组3级": "..."},
                ...
            ],
            "潜能提升": {"潜能提升2": "...", "潜能提升3": "...", "备注": "..."},
            "后勤技能": [...],
            "模组": {...}
        }

注意事项:
    - 部分干员名称与PRTS页面名称可能不同(如"赫默"而非"赫墨")
    - 天赋中的潜能提升显示格式: "原始数值(潜能X: 变化值)"
    - 模组数据如为空则显示"该干员没有模组提升"
    - 潜能提升key格式为"潜能提升X"（X为潜能等级）
"""

import requests
import json
import re
import time
from bs4 import BeautifulSoup

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import PRTS_HEADERS


def get_all_operators():
    url = 'https://prts.wiki/w/干员一览/干员id'
    headers = PRTS_HEADERS

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        text = resp.text

        match = re.search(r'sortId,name,rarity,approach,date', text)
        if not match:
            print("无法找到CSV数据")
            return []

        csv_text = text[match.start():]
        lines = csv_text.strip().split('\n')

        operators = []
        for line in lines[1:]:
            parts = line.split(',')
            if len(parts) >= 2:
                name = parts[1].strip()
                if name and not name.startswith('预备干员') and '(' not in name:
                    if '·' not in name and len(name) < 15:
                        operators.append(name)

        return operators
    except Exception as e:
        print(f"Error getting operator list: {e}")
        return []


def parse_attack_range_to_string(svg):
    if not svg:
        return None

    defs = svg.find('defs')
    rects = {}
    if defs:
        for rect in defs.find_all('rect'):
            rect_id = rect.get('id')
            fill = rect.get('fill')
            rects[rect_id] = fill

    uses = svg.find_all('use')
    cells = {}
    for use in uses:
        href = use.get('xlink:href', '').replace('#', '')
        x = float(use.get('x', '0'))
        y = float(use.get('y', '0'))

        if href in rects:
            fill = rects[href]
            cells[(x, y)] = fill

    if not cells:
        return None

    xs = [c[0] for c in cells.keys()]
    ys = [c[1] for c in cells.keys()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    grid_width = int((max_x - min_x) / 22) + 1
    grid_height = int((max_y - min_y) / 22) + 1

    grid = [['.' for _ in range(grid_width)] for _ in range(grid_height)]

    for (x, y), fill in cells.items():
        col = int((x - min_x) / 22)
        row = int((y - min_y) / 22)

        if fill == '#27a6f3':
            grid[row][col] = 'T'
        elif fill == 'none':
            grid[row][col] = 'X'

    return [''.join(row) for row in grid]


def get_operator_range(operator_name):
    url = f'https://prts.wiki/w/{operator_name}'
    headers = PRTS_HEADERS

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')

        tables = soup.find_all('table', class_='wikitable')
        target_table = None

        for table in tables:
            cls = table.get('class', [])
            if 'nomobile' in cls or 'nodesktop' in cls:
                if '精英0' in table.get_text() and '精英1' in table.get_text():
                    target_table = table
                    break

        if not target_table:
            for table in tables:
                cls = table.get('class', [])
                if 'nomobile' in cls or 'nodesktop' in cls:
                    if '精英0' in table.get_text():
                        target_table = table
                        break

        if not target_table:
            return None

        tds = target_table.find_all('td')
        ranges = []
        for td in tds:
            svg = td.find('svg')
            grid = parse_attack_range_to_string(svg)
            if grid:
                ranges.append(grid)

        return ranges if len(ranges) > 0 else None

    except Exception as e:
        print(f"Error fetching {operator_name}: {e}")
        return None


def merge_ranges(ranges):
    if not ranges or len(ranges) == 0:
        return {}

    result = {}

    if len(ranges) == 1:
        result['精英0范围'] = ranges[0]
    elif len(ranges) == 2:
        range_strs = [str(ranges[0]), str(ranges[1])]
        if range_strs[0] == range_strs[1]:
            result['精英0_精英1范围'] = ranges[0]
        else:
            result['精英0范围'] = ranges[0]
            result['精英1范围'] = ranges[1]
    else:
        range_strs = [str(r) for r in ranges]
        if range_strs[0] == range_strs[1] == range_strs[2]:
            result['精英0_精英1_精英2范围'] = ranges[0]
        elif range_strs[1] == range_strs[2]:
            result['精英0范围'] = ranges[0]
            result['精英1_精英2范围'] = ranges[1]
        else:
            result['精英0范围'] = ranges[0]
            result['精英1范围'] = ranges[1]
            result['精英2范围'] = ranges[2]

    return result


def get_wikitext(operator_name):
    url = f"https://prts.wiki/api.php?action=parse&page={operator_name}&prop=wikitext&format=json"
    headers = PRTS_HEADERS
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        return data['parse']['wikitext']['*']
    except Exception as e:
        print(f"获取{operator_name}失败: {e}")
        return None

def get_html(operator_name):
    url = f"https://prts.wiki/w/{operator_name}"
    headers = PRTS_HEADERS
    try:
        response = requests.get(url, headers=headers, timeout=10)
        return response.text
    except Exception as e:
        print(f"获取{operator_name} HTML失败: {e}")
        return None

def extract_html_info(html):
    info = {}
    if not html:
        return info

    match = re.search(r'获得方式\s*</th>\s*<td[^>]*>([\s\S]*?)</td>', html)
    if match:
        text = match.group(1)
        text = re.sub(r'<br\s*/?>', ' ', text)
        text = re.sub(r'<[^>]+>', '', text)
        info["获得方式"] = text.strip()

    match = re.search(r'上线时间\s*</th>\s*<td[^>]*>([\s\S]*?)</td>', html)
    if match:
        text = match.group(1)
        text = re.sub(r'<br\s*/?>', ' ', text)
        text = re.sub(r'<[^>]+>', '', text)
        info["上线时间"] = text.strip()

    return info

def extract_attributes_from_wikitext(wikitext):
    attrs = {}

    def extract_stat(stage_key, stat_type):
        pattern = rf'{stage_key}_{stat_type}=(\d+)'
        match = re.search(pattern, wikitext)
        return match.group(1) if match else None

    attrs['生命上限_攻击_防御_法术抗性'] = {}

    stages = [
        ('精英0_1级', '精英0_1级'),
        ('精英0_满级', '精英0_满级'),
        ('精英1_满级', '精英1_满级'),
        ('精英2_满级', '精英2_满级')
    ]

    for json_key, wiki_key in stages:
        hp = extract_stat(wiki_key, '生命上限')
        atk = extract_stat(wiki_key, '攻击')
        df = extract_stat(wiki_key, '防御')
        res = extract_stat(wiki_key, '法术抗性')
        if hp is not None:
            attrs['生命上限_攻击_防御_法术抗性'][json_key] = f"{hp} {atk} {df} {res}"

    tr_hp = extract_stat('信赖加成', '生命上限')
    tr_atk = extract_stat('信赖加成', '攻击')
    tr_df = extract_stat('信赖加成', '防御')
    tr_res = extract_stat('信赖加成', '法术抗性')
    if tr_hp is not None or tr_atk is not None or tr_df is not None or tr_res is not None:
        attrs['生命上限_攻击_防御_法术抗性']['信赖加成上限'] = f"{tr_hp or 0} {tr_atk or 0} {tr_df or 0} {tr_res or 0}"

    return attrs

def clean_text(text, max_iterations=20):
    if not text:
        return text

    text = re.sub(r'<br\s*/?>', ' ', text)
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'<!--.*?-->', '', text)
    text = re.sub(r'<!--.*?>', '', text)
    text = re.sub(r'<.*?>', '', text)

    text = re.sub(r'问号大小=\d+px\|内容=', '', text)
    text = re.sub(r'内容=[^]]+(?=\|$|\])', '', text)
    text = re.sub(r'内容=', '', text)

    text = re.sub(r'\s*y-\d+', '', text)

    text = re.sub(r'\{\{异常效果\|([^}|]+)\}\}', r'\1', text)
    text = re.sub(r'\{\{popup内容=[^}]*\}\}', '', text)
    text = re.sub(r'\{\{popup/[^}]*\}\}', '', text)
    text = re.sub(r'\{\{mdi\|[^}]+\}\}', '', text)
    text = re.sub(r'\{\{特殊机制[^}]*\}\}', '', text)

    for _ in range(max_iterations):
        original = text

        text = re.sub(r'\{\{color\|#?[0-9A-Fa-f]+\|([^}]+)\}\}', r'\1', text)
        text = re.sub(r'\{\{color\|([^|]+)\|([^}]+)\}\}', r'\2', text)

        text = re.sub(r'\{\{变动数值lite\|down\|蓝\|(\d+)\}\}', r'-\1', text)
        text = re.sub(r'\{\{变动数值lite\|down\|蓝\|([^}]+)\}\}', r'-\1', text)
        text = re.sub(r'\{\{变动数值lite\|up\|蓝\|([^}]+)\}\}', r'\1', text)
        text = re.sub(r'\{\{变动数值lite\|[^|]*\|[^|]*\|([^}]+)\}\}', r'\1', text)
        text = re.sub(r'\{\{变动数值lite\|[^|]+\|[^|]+\|([^}]+)\}\}', r'\1', text)

        text = re.sub(r'\{\{术语\|[^|]+\|([^}]+)\}\}', r'\1', text)

        text = re.sub(r'\{\{修正\|([^{}|]+)[^{}]*\}\}', r'\1', text)

        text = re.sub(r'\{\{\*\|([^|{}]+)\|([^}]+)\}\}', r'\2', text)
        text = re.sub(r'\{\{\*\*\|[^|]*\|([^}]+)\}\}', r'\1', text)
        text = re.sub(r'\{\{\+\|([^|{}]+)\|([^}]+)\}\}', r'\2', text)
        text = re.sub(r'\{\{异常效果\|([^|]+)\|([^}]+)\}\}', r'\1\2', text)

        text = re.sub(r'\{\{\+\+\|\(([^)]+)\)\|([^}]+)\}\}', r'-\1', text)
        text = re.sub(r'\{\{\*\*\|\(([^)]+)\)\|([^}]+)\}\}', r'-\1', text)
        text = re.sub(r'\{\{\+\+\|\(([^)]+)\)\|', r'-\1', text)
        text = re.sub(r'\{\{\*\*\|\(([^)]+)\)\|', r'-\1', text)
        text = re.sub(r'\{\{\*\*\|([^|{}]+)\|', r'\1', text)
        text = re.sub(r'\{\{\*\|([^|{}]+\|)+([^|{}]+)\|', r'\2', text)

        text = re.sub(r'\{\{±\|([^|]+\|)+([^}]+)\}\}', r'\2', text)

        text = re.sub(r'\{\{[^|{}]+\}\}', '', text)

        if original == text:
            break

    text = re.sub(r'\[\[([^|\]]+\|)?([^\]]+)\]\]', r'\2', text)
    text = re.sub(r']]', '', text)

    text = re.sub(r"'''([^']+)'''", r'\1', text)

    text = re.sub(r'\{\{[^|}]+\|', '', text)
    text = text.replace('}}', '')

    text = re.sub(r'--+', '-', text)

    text = re.sub(r'\s+', ' ', text)

    text = re.sub(r'\|([a-zA-Z]+)=', r'\1=', text)

    return text.strip()

def parse_talents(wikitext):
    talents = []
    talent_start = wikitext.find('==天赋==')
    talent_end = wikitext.find('==潜能提升==')
    if talent_start == -1 or talent_end == -1:
        return talents

    talent_section = wikitext[talent_start:talent_end]

    blocks = re.split(r'\{\{天赋列表3\s*\n', talent_section)
    for block in blocks[1:]:
        brace_count = 1
        end_idx = 0
        for i, char in enumerate(block):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i
                    break
        block_content = block[:end_idx]

        potential_enhancements = {}
        potential_level_match = re.search(r'\|潜能增强=(\d+)', block_content)
        if potential_level_match:
            pot_level = potential_level_match.group(1).strip()
            for idx in ['1', '2', '3', '4', '5', '6', '7']:
                enhanced_effect_match = re.search(rf'\|潜能增强_天赋{idx}效果=([^\n]+)', block_content)
                if enhanced_effect_match:
                    potential_enhancements[idx] = (pot_level, enhanced_effect_match.group(1).strip())

        entries_by_talent = {}
        for idx in ['1', '2', '3', '4', '5', '6', '7']:
            name_match = re.search(rf'\|天赋{idx}=([^\n|]+)', block_content)
            cond_match = re.search(rf'\|天赋{idx}条件=([^\n]+)', block_content)
            effect_match = re.search(rf'\|天赋{idx}效果=([^\n]+)', block_content)
            if name_match and cond_match and effect_match:
                name = clean_text(name_match.group(1).strip())
                cond = clean_text(cond_match.group(1).strip())
                effect = clean_text(effect_match.group(1))
                if name not in entries_by_talent:
                    entries_by_talent[name] = []
                entries_by_talent[name].append((idx, cond, effect))

        def compute_enhanced_text(orig_effect, enhanced_raw, pot_level):
            enhanced = clean_text(enhanced_raw)

            pattern = r'(\d+)([%秒分]?)[（\(]([+-]?\d+)([%秒分]?)[）\)]'

            def replace_one(match):
                enhanced_num = int(match.group(1))
                suffix_before = match.group(2)
                change_str = match.group(3)
                suffix_after = match.group(4)
                change_val = int(change_str)
                calculated_orig = enhanced_num - change_val
                final_suffix = suffix_before if suffix_before else suffix_after
                return f"{calculated_orig}{final_suffix}（潜能{pot_level}：{change_str}{suffix_after}）"

            result = re.sub(pattern, replace_one, enhanced)
            return result

        for talent_name, raw_entries in entries_by_talent.items():
            talent_data = {"名称": talent_name}

            all_effects = {}

            for idx, cond, effect in raw_entries:
                if idx in potential_enhancements:
                    pot_level, enhanced_raw = potential_enhancements[idx]
                    effect = compute_enhanced_text(effect, enhanced_raw, pot_level)
                all_effects[cond] = effect

            for cond, effect in all_effects.items():
                talent_data[cond] = effect

            remark_match = re.search(r'\|备注=([^\n]+)', block_content)
            if remark_match:
                raw_remark = remark_match.group(1).strip()
                if raw_remark and '※' in raw_remark:
                    remark_text = clean_text(raw_remark)
                    remark_text = None if not remark_text else remark_text
                elif raw_remark:
                    remark_text = clean_text(raw_remark)
                    remark_text = None if not remark_text else remark_text
                else:
                    remark_text = None
            else:
                remark_text = None
            talent_data["备注"] = remark_text

            if len(talent_data) > 2:
                talents.append(talent_data)

    return talents

def parse_logistics(wikitext, html):
    logistics = []

    if not html:
        logistics_match = re.search(r'==后勤技能==\s*\{\{后勤技能\s*(.*?)\}\}', wikitext, re.DOTALL)
        if not logistics_match:
            return logistics
        return logistics

    soup = BeautifulSoup(html, 'html.parser')

    for elem in soup.find_all(style=lambda v: v and 'display:none' in str(v)):
        elem.decompose()
    for elem in soup.find_all('span', attrs={'data-size': True}):
        elem.decompose()
    for elem in soup.find_all('span', class_='mc-tooltips'):
        elem.unwrap()

    logistics_header = soup.find(id='后勤技能')
    if not logistics_header:
        return logistics

    tables = logistics_header.find_all_next('table')
    for table in tables[:3]:
        rows = table.find_all('tr')[1:]
        for row in rows:
            cells = row.find_all('td')
            if len(cells) >= 5:
                condition = cells[0].get_text(strip=True)
                skill_name = cells[2].get_text(strip=True)
                room = cells[3].get_text(strip=True)
                description = cells[4].get_text(separator='', strip=True)
                description = re.sub(r'\s+', ' ', description).strip()

                if condition and skill_name:
                    logistics.append({
                        "条件": condition,
                        "技能": skill_name,
                        "房间": room,
                        "描述": description
                    })

    cleaned_logistics = []
    for item in logistics:
        new_item = {}
        for key, value in item.items():
            new_key = clean_text(key)
            new_value = clean_text(value) if isinstance(value, str) else value
            new_item[new_key] = new_value
        cleaned_logistics.append(new_item)

    return cleaned_logistics

def parse_modules(wikitext):
    modules = {}
    module_start = wikitext.find('==模组==')
    module_end = wikitext.find('==相关道具==')
    if module_start == -1 or module_end == -1:
        return modules

    module_section = wikitext[module_start:module_end]

    module_blocks = re.findall(r'===([^=]+)===\s*\n<section begin=专属模组 />\s*\n{{模组\s*\n(.*?)\n\}}', module_section, re.DOTALL)

    for name, content in module_blocks:
        module_name = name.strip()
        if module_name.endswith('证章'):
            continue

        module_data = {}

        def extract_stats(content, suffix=''):
            stats = []
            hp = re.search(rf'\|生命{suffix}=(\d+)', content)
            atk = re.search(rf'\|攻击{suffix}=(\d+)', content)
            df = re.search(rf'\|防御{suffix}=(\d+)', content)
            res = re.search(rf'\|法术抗性{suffix}=(\d+)', content)
            if hp:
                stats.append(f"生命+{hp.group(1)}")
            if atk:
                stats.append(f"攻击+{atk.group(1)}")
            if df:
                stats.append(f"防御+{df.group(1)}")
            if res:
                stats.append(f"法术抗性+{res.group(1)}")
            return " ".join(stats) if stats else None

        stage1_stats = extract_stats(content)
        if stage1_stats:
            module_data["阶段1"] = {"属性": stage1_stats, "描述": ""}

        trait = re.search(r'\|特性=([^\n]+)', content)
        if trait:
            desc = clean_text(trait.group(1))
            if module_data.get("阶段1"):
                module_data["阶段1"]["描述"] = f"特性追加：{desc}"

        stage2_stats = extract_stats(content, "2")
        if stage2_stats:
            module_data["阶段2"] = {"属性": stage2_stats, "描述": ""}

        talent2 = re.search(r'\|天赋2=([^\n]+)', content)
        if talent2:
            desc = clean_text(talent2.group(1))
            if module_data.get("阶段2"):
                module_data["阶段2"]["描述"] = desc

        stage3_stats = extract_stats(content, "3")
        if stage3_stats:
            module_data["阶段3"] = {"属性": stage3_stats, "描述": ""}

        talent3 = re.search(r'\|天赋3=([^\n]+)', content)
        if talent3:
            desc = clean_text(talent3.group(1))
            if module_data.get("阶段3"):
                module_data["阶段3"]["描述"] = desc

        tasks = []
        for i in range(1, 5):
            task = re.search(rf'\|任务{i}=([^\n]+)', content)
            if task:
                tasks.append(clean_text(task.group(1)))
        if tasks:
            module_data["解锁任务"] = " ".join(tasks)

        if module_data:
            modules[module_name] = module_data

    return modules

def parse_potential(wikitext):
    potential = {}
    potential_start = wikitext.find('==潜能提升==')
    potential_end = wikitext.find('==后勤技能==')
    
    if potential_end == -1:
        potential_end = wikitext.find('==模组==')
    if potential_end == -1:
        potential_end = wikitext.find('==语音记录==')
    if potential_end == -1:
        potential_end = len(wikitext)
    
    if potential_start == -1:
        return potential

    potential_section = wikitext[potential_start:potential_end]

    note_match = re.search(r'该干员无法使用通用信物来提升潜能。', potential_section)
    if note_match:
        potential["备注"] = "该干员无法使用通用信物来提升潜能。"

    note_match2 = re.search(r'该干员无法提升潜能', potential_section)
    if note_match2:
        potential["备注"] = "该干员无法提升潜能"

    block_match = re.search(r'\{\{潜能提升\s*\n(.*?)\}\}', potential_section, re.DOTALL)
    if not block_match:
        if not potential:
            potential["备注"] = "该干员无潜能提升"
        return potential

    block = block_match.group(1)

    for match in re.finditer(r'\|潜能(\d+)=([^\n]+)', block):
        level = match.group(1).strip()
        effect = clean_text(match.group(2))
        if level and effect:
            potential[f"潜能提升{level}"] = effect

    if not potential:
        potential["备注"] = "该干员无潜能提升"

    return potential

def parse_skills(wikitext):
    skills_list = []
    skills_section_start = wikitext.find('==技能==')
    if skills_section_start == -1:
        return skills_list

    skills_section = wikitext[skills_section_start:]

    if '该干员没有技能' in skills_section:
        return skills_list

    skill_headers = list(re.finditer(r"'''技能\d+（[^）]+）'''", skills_section))

    for i, header_match in enumerate(skill_headers):
        header_end = header_match.end()
        next_header_start = skill_headers[i+1].start() if i+1 < len(skill_headers) else len(skills_section)
        block_section = skills_section[header_end:next_header_start].strip()

        brace_count = 0
        block_end = 0
        for j, char in enumerate(block_section):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    block_end = j
                    break

        block = block_section[:block_end+1]

        skill_name_match = re.search(r'\|技能名=([^\n]+)', block)
        skill_type1_match = re.search(r'\|技能类型1=([^\n]+)', block)
        skill_type2_match = re.search(r'\|技能类型2=([^\n]+)', block)
        skill_desc7_match = re.search(r'\|技能7描述=([^\n]+)', block)
        skill_init7_match = re.search(r'\|技能7初始=([^\n]+)', block)
        skill_cost7_match = re.search(r'\|技能7消耗=([^\n]+)', block)
        skill_duration7_match = re.search(r'\|技能7持续=([^\n]+)', block)
        skill_init3_match = re.search(r'\|技能专精3初始=([^\n]+)', block)
        skill_cost3_match = re.search(r'\|技能专精3消耗=([^\n]+)', block)
        skill_duration3_match = re.search(r'\|技能专精3持续=([^\n]+)', block)
        skill_desc3_match = re.search(r'\|技能专精3描述=([^\n]+)', block)
        skill_remark_match = re.search(r'\|备注=([^\n]+)', block)

        if skill_name_match:
            skill_data = {
                "名称": clean_text(skill_name_match.group(1)),
                "类型": "",
                "Level 7_初始_消耗_持续": "",
                "Rank Ⅲ_初始_消耗_持续": "",
                "备注": None
            }

            type_parts = []
            if skill_type1_match:
                type_parts.append(skill_type1_match.group(1).strip())
            if skill_type2_match:
                type_parts.append(skill_type2_match.group(1).strip())
            if type_parts:
                skill_data["类型"] = " ".join(type_parts)

            def get_val(match):
                if not match or not match.group(1).strip():
                    return "0"
                return match.group(1).strip()

            def format_skill_data(desc, init, cost, duration):
                if not desc:
                    return ""
                desc = re.sub(r'<br\s*/?>', '|||', desc)
                parts = desc.split('|||')
                if len(parts) > 1:
                    desc = parts[-1].strip()
                else:
                    desc = parts[0].strip()
                desc = clean_text(desc)
                split_pos = desc.rfind('|')
                if split_pos > 0:
                    possible_nums = desc[split_pos+1:].strip().split()
                    if len(possible_nums) >= 3:
                        try:
                            int(possible_nums[0])
                            int(possible_nums[1])
                            int(possible_nums[2])
                            desc = desc[:split_pos].strip()
                        except:
                            pass
                return f"{desc} | {init} {cost} {duration}".strip()

            desc7 = clean_text(skill_desc7_match.group(1)) if skill_desc7_match else ""
            init7 = get_val(skill_init7_match)
            cost7 = get_val(skill_cost7_match)
            duration7 = get_val(skill_duration7_match)
            skill_data["Level 7_初始_消耗_持续"] = format_skill_data(desc7, init7, cost7, duration7)

            desc3 = clean_text(skill_desc3_match.group(1)) if skill_desc3_match else ""
            init3 = get_val(skill_init3_match)
            cost3 = get_val(skill_cost3_match)
            duration3 = get_val(skill_duration3_match)
            skill_data["Rank Ⅲ_初始_消耗_持续"] = format_skill_data(desc3, init3, cost3, duration3)

            if skill_remark_match:
                remark_text = clean_text(skill_remark_match.group(1))
                if '※' in remark_text:
                    skill_data["备注"] = remark_text
                elif remark_text:
                    skill_data["备注"] = None
                else:
                    skill_data["备注"] = None
            else:
                skill_data["备注"] = None

            skills_list.append(skill_data)

    return skills_list

def extract_basic_info(wikitext):
    info = {}

    charinfo_start = wikitext.find('{{CharinfoV2')
    if charinfo_start >= 0:
        brace_count = 0
        for i, char in enumerate(wikitext[charinfo_start:]):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    charinfo_end = charinfo_start + i + 1
                    content_block = wikitext[charinfo_start:charinfo_end]
                    break

        fields = {
            '职业': '职业',
            '分支': '分支',
            '稀有度': '稀有度',
            '特性': '特性',
            '再部署': '再部署',
            '部署费用': '部署费用',
            '阻挡数': '阻挡数',
            '攻击速度': '攻击速度'
        }

        for wiki_field, json_key in fields.items():
            pattern = rf'\|\s*{wiki_field}\s*=\s*([^\n]+)'
            match = re.search(pattern, content_block)
            if match:
                info[json_key] = clean_text(match.group(1))

        for field in ['职业', '稀有度', '分支', '再部署', '部署费用', '阻挡数', '攻击速度']:
            if field not in info:
                pattern = rf'{field}=([^\n]+)'
                match = re.search(pattern, wikitext)
                if match:
                    info[field] = match.group(1).strip()

    return info

def extract_wiki_info(wikitext):
    info = {}

    artist_match = re.search(r'\|\s*画师\s*=\s*([^\n]+)', wikitext)
    if artist_match:
        info['画师'] = clean_text(artist_match.group(1))

    voice_actors = {}
    voice_fields = {
        '中文配音': '中文',
        '日文配音': '日文',
        '韩文配音': '韩文',
        '英文配音': '英文'
    }
    for wiki_field, display_name in voice_fields.items():
        pattern = rf'\|\s*{wiki_field}\s*=\s*([^\n]+)'
        match = re.search(pattern, wikitext)
        if match:
            value = clean_text(match.group(1))
            if value:
                voice_actors[display_name] = value
    if voice_actors:
        info['配音'] = voice_actors

    faction_match = re.search(r'\|\s*所属势力\s*=\s*([^\n]+)', wikitext)
    if faction_match:
        info['所属势力'] = clean_text(faction_match.group(1))

    page_name_match = re.search(r'\{\{干员页面名\|([^|]+)\|([^|]+)\|([^|]+)\}\}', wikitext)
    if page_name_match:
        info['干员英文名'] = page_name_match.group(2).strip()
        info['干员日文名'] = page_name_match.group(3).strip()

    variant_match = re.search(r'\{\{异格干员\|原型=([^{}|]+)', wikitext)
    if variant_match:
        prototype = variant_match.group(1).strip()
        if prototype != '{{BASEPAGENAME}}':
            info['异格干员_原型'] = prototype

    return info

def parse_operator(operator_name):
    wikitext = get_wikitext(operator_name)
    if not wikitext:
        return None

    html = get_html(operator_name)
    html_info = extract_html_info(html)
    wikitext_attrs = extract_attributes_from_wikitext(wikitext)
    wiki_info = extract_wiki_info(wikitext)
    basic_info = extract_basic_info(wikitext)

    result = {}

    result["干员名"] = operator_name
    en_name = wiki_info.get('干员英文名', '')
    jp_name = wiki_info.get('干员日文名', '')
    if en_name or jp_name:
        combined_name = f"{en_name} {jp_name}".strip()
        parts = combined_name.split()
        unique_parts = []
        seen = set()
        for part in parts:
            if part not in seen:
                unique_parts.append(part)
                seen.add(part)
        result["干员外文名"] = " ".join(unique_parts)
    else:
        result["干员外文名"] = ""
    if wiki_info.get('异格干员_原型'):
        result["异格干员_原型"] = wiki_info.get('异格干员_原型', '')
    rarity = basic_info.get("稀有度", "")
    if rarity:
        try:
            result["星级"] = str(int(rarity) + 1)
        except:
            result["星级"] = str(rarity)
    else:
        result["星级"] = ""
    result["职业"] = basic_info.get("职业", "")
    result["分支"] = basic_info.get("分支", "")
    result["特性"] = basic_info.get("特性", "")
    faction = wiki_info.get("所属势力", "")
    result["所属势力"] = faction if faction else None
    result["隐藏势力"] = "无"
    result["获得方式"] = html_info.get("获得方式", "")
    result["上线时间"] = html_info.get("上线时间", "")
    result["画师"] = wiki_info.get("画师", "")
    result["配音"] = wiki_info.get("配音", {})

    if wikitext_attrs.get('生命上限_攻击_防御_法术抗性'):
        result["生命上限_攻击_防御_法术抗性"] = wikitext_attrs['生命上限_攻击_防御_法术抗性']

    result["再部署"] = basic_info.get("再部署", "")
    result["部署费用"] = basic_info.get("部署费用", "")
    result["阻挡数"] = basic_info.get("阻挡数", "")
    result["攻击速度"] = basic_info.get("攻击速度", "")

    range_result = get_operator_range(operator_name)
    if range_result:
        result["攻击范围"] = merge_ranges(range_result)

    result["技能"] = parse_skills(wikitext)
    result["天赋"] = parse_talents(wikitext)
    
    potential_result = parse_potential(wikitext)
    result["潜能提升"] = potential_result
    
    result["后勤技能"] = parse_logistics(wikitext, html)
    modules = parse_modules(wikitext)
    result["模组"] = modules if modules else "该干员没有模组提升"

    return result

def find_operator_in_json(operator_name, json_data):
    for op in json_data:
        if op.get("干员名") == operator_name:
            return op
    return None

if __name__ == "__main__":
    print('正在获取干员列表...')
    test_operators = get_all_operators()
    print(f'找到 {len(test_operators)} 个干员')

    results = []
    fail_count = 0

    for i, name in enumerate(test_operators):
        print(f"[{i+1}/{len(test_operators)}] 正在爬取: {name}")
        result = parse_operator(name)
        if result:
            results.append(result)
            print(f"成功爬取: {name}")
        else:
            fail_count += 1
            print(f"爬取失败: {name}")
        time.sleep(0.3)

    with open('all_operators.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n已保存 {len(results)} 个干员到 all_operators.json")
    print(f"失败 {fail_count} 个")