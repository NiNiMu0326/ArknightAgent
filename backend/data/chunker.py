import re
import json
import shutil
from typing import List, Dict, Any
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = BASE_DIR / "data"
CHUNKS_DIR = BASE_DIR / "chunks"

# ========== 统一配置常量 ==========
CHUNK_CONFIG = {
    "min_size": 1500,          # 最小chunk大小 (用于合并判断)
    "target_size": 4000,       # 最大累积大小 (超过此值不再合并)
    "max_chunk_size": 6000,    # 最大chunk大小
}

def json_to_text(obj: Dict[str, Any]) -> str:
    lines = []
    for key, value in obj.items():
        if value is None or value == "":
            continue
        if isinstance(value, list):
            value = "、".join(str(v) for v in value)
        lines.append(f"{key}: {value}")
    return "\n".join(lines)

def load_all_operators(path: str) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_all_enemies(path: str) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_operators_summary(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_enemies_summary(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_game_terms(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_memes(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_gameplay(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def load_md_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def split_by_sections(content: str, header_level: int = 2) -> List[tuple]:
    """Split markdown content by ## or ### headers. Returns [(section_name, content), ...]"""
    pattern = r'\n' + '#' * header_level + r'\s+(.+?)\n'
    
    parts = re.split(pattern, content)
    result = []
    if parts[0].strip() and parts[0].strip() != '#':
        result.append(("开头", parts[0]))
    for i in range(1, len(parts), 2):
        if i+1 < len(parts):
            result.append((parts[i].strip(), parts[i+1]))
    return result


def chunk_operators_file(content: str, filename: str, global_idx: int) -> List[Dict]:
    """Split operator md by ## sections with real-time splitting and merging.

    规则：
    - 章节 <= max_size：整体保留为一个chunk，不拆分
    - 章节 > max_size：按段落拆分，实时处理每一块
    - 合并条件：两个块都 < min_size 且合并后 <= max_size
    - chunk_id 格式：operators_0001_01_01（干员序号_章节序号_章节内序号）
      - 如果章节没有拆分，最后的 _01 省略：operators_0001_01
    """
    sections = split_by_sections(content)
    sections = _promote_h1_title(sections)

    min_size = CHUNK_CONFIG["min_size"]
    max_size = CHUNK_CONFIG["max_chunk_size"]

    chunks = []
    sub_chunk_counter = {}

    for i, (sec_name, sec_content) in enumerate(sections):
        if not sec_content.strip():
            continue

        sec_content = sec_content.strip()
        if sec_name not in sub_chunk_counter:
            sub_chunk_counter[sec_name] = 0

        # 章节 <= max_size：整体作为一个chunk
        if len(sec_content) <= max_size:
            _process_chunk_realtime(sec_content, chunks, global_idx, min_size, max_size, i + 1, sec_name, sub_chunk_counter, filename)
        else:
            # 章节 > max_size：按段落拆分，实时处理
            paragraphs = sec_content.split("\n\n")
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                if len(para) > max_size:
                    sentences = re.split(r'(?<=[。！？])\s*', para)
                    for sent in sentences:
                        sent = sent.strip()
                        if not sent:
                            continue
                        _process_chunk_realtime(sent, chunks, global_idx, min_size, max_size, i + 1, sec_name, sub_chunk_counter, filename)
                else:
                    _process_chunk_realtime(para, chunks, global_idx, min_size, max_size, i + 1, sec_name, sub_chunk_counter, filename)

    return chunks


def _process_chunk_realtime(content: str, chunks: list, global_idx: int, min_size: int, max_size: int, sec_idx: int, sec_name: str, sub_chunk_counter: dict, filename: str = None):
    """实时处理单个 chunk：检查是否需要合并到前一块"""
    content = content.strip()
    if not content:
        return

    # 如果内容太大，先拆分
    if len(content) > max_size:
        paragraphs = content.split("\n\n")
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(para) > max_size:
                sentences = re.split(r'(?<=[。！？])\s*', para)
                for sent in sentences:
                    sent = sent.strip()
                    if sent:
                        _process_chunk_realtime(sent, chunks, global_idx, min_size, max_size, sec_idx, sec_name, sub_chunk_counter, filename)
            else:
                _process_chunk_realtime(para, chunks, global_idx, min_size, max_size, sec_idx, sec_name, sub_chunk_counter, filename)
        return

    # 合并判断：当前块或前一块 < min_size，且合并后 <= max_size，且累积大小 < target_size
    should_merge = False
    if chunks:
        prev_size = len(chunks[-1]["content"])
        curr_size = len(content)
        target_size = CHUNK_CONFIG["target_size"]

        if (prev_size < min_size or curr_size < min_size) and curr_size + prev_size + 2 <= max_size and prev_size < target_size:
            should_merge = True

    if should_merge:
        # 合并到前一个 chunk
        if sec_name:
            chunks[-1]["content"] += "\n\n## " + sec_name + "\n\n" + content
    else:
        # 新建 chunk，增加该章节的子块计数
        sub_chunk_counter[sec_name] = sub_chunk_counter.get(sec_name, 0) + 1
        sub_idx = sub_chunk_counter[sec_name]

        # 格式：operators_0001_01_01 或 operators_0001_01
        if sub_idx == 1:
            chunk_id = f"operators_{global_idx:04d}_{sec_idx:02d}"
        else:
            chunk_id = f"operators_{global_idx:04d}_{sec_idx:02d}_{sub_idx:02d}"

        chunks.append({
            "chunk_id": chunk_id,
            "section": sec_name,
            "content": content,
            "source_file": filename or ""
        })

def _extract_h1_title(sec_content: str) -> tuple:
    """If sec_content starts with an H1 '# Title', strip it and return (title, remaining)."""
    lines = sec_content.split('\n')
    for idx, line in enumerate(lines):
        if re.match(r'^#\s+.+$', line.strip()):
            h1 = line.strip()[1:].strip()
            remaining = '\n'.join(lines[idx+1:]).strip()
            return h1, remaining
    return None, sec_content

def _promote_h1_title(sections: List[tuple]) -> List[tuple]:
    """If the first section is still named '开头' and its content starts with H1, promote H1 as section name.

    When the first section already has a meaningful name, the H1 in its content
    belongs to the story, not to a document-level title.
    """
    if not sections:
        return sections
    sec_name, sec_content = sections[0]
    if sec_name != "开头":
        return sections
    h1_title, remaining = _extract_h1_title(sec_content)
    if h1_title:
        sections = [(h1_title, remaining)] + list(sections[1:])
    return sections

def chunk_story_file(content: str, filename: str, global_idx: int) -> List[Dict]:
    """Split story md by ## sections with real-time splitting and merging.

    规则：
    - 章节 <= max_size：整体保留为一个chunk，不拆分
    - 章节 > max_size：按段落拆分，实时处理每一块
    - 合并条件：当前块或前一块 < min_size，且合并后 <= max_size，且累积 < target_size
    - chunk_id 格式：stories_0001_01
    """
    sections = split_by_sections(content)
    sections = _promote_h1_title(sections)

    min_size = CHUNK_CONFIG["min_size"]
    max_size = CHUNK_CONFIG["max_chunk_size"]

    chunks = []
    sub_chunk_counter = {}

    for i, (sec_name, sec_content) in enumerate(sections):
        if not sec_content.strip():
            continue

        sec_content = sec_content.strip()
        if sec_name not in sub_chunk_counter:
            sub_chunk_counter[sec_name] = 0

        # 章节 <= max_size：整体作为一个chunk
        if len(sec_content) <= max_size:
            _process_story_chunk(sec_content, chunks, min_size, max_size, i + 1, sec_name, sub_chunk_counter, filename, global_idx)
        else:
            # 章节 > max_size：按段落拆分，实时处理
            paragraphs = sec_content.split("\n\n")
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                if len(para) > max_size:
                    sentences = re.split(r'(?<=[。！？])\s*', para)
                    for sent in sentences:
                        sent = sent.strip()
                        if not sent:
                            continue
                        _process_story_chunk(sent, chunks, min_size, max_size, i + 1, sec_name, sub_chunk_counter, filename, global_idx)
                else:
                    _process_story_chunk(para, chunks, min_size, max_size, i + 1, sec_name, sub_chunk_counter, filename, global_idx)

    return chunks


def _process_story_chunk(content: str, chunks: list, min_size: int, max_size: int, sec_idx: int, sec_name: str, sub_chunk_counter: dict, filename: str, global_idx: int):
    """实时处理单个 chunk：检查是否需要合并到前一块"""
    content = content.strip()
    if not content:
        return

    # 如果内容太大，先拆分
    if len(content) > max_size:
        paragraphs = content.split("\n\n")
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(para) > max_size:
                sentences = re.split(r'(?<=[。！？])\s*', para)
                for sent in sentences:
                    sent = sent.strip()
                    if sent:
                        _process_story_chunk(sent, chunks, min_size, max_size, sec_idx, sec_name, sub_chunk_counter, filename, global_idx)
            else:
                _process_story_chunk(para, chunks, min_size, max_size, sec_idx, sec_name, sub_chunk_counter, filename, global_idx)
        return

    # 合并判断：当前块或前一块 < min_size，且合并后 <= max_size，且累积 < target_size
    should_merge = False
    if chunks:
        prev_size = len(chunks[-1]["content"])
        curr_size = len(content)
        target_size = CHUNK_CONFIG["target_size"]

        if (prev_size < min_size or curr_size < min_size) and curr_size + prev_size + 2 <= max_size and prev_size < target_size:
            should_merge = True

    if should_merge:
        # 合并到前一个 chunk
        if sec_name:
            chunks[-1]["content"] += "\n\n## " + sec_name + "\n\n" + content
    else:
        # 新建 chunk，增加该章节的子块计数
        sub_chunk_counter[sec_name] = sub_chunk_counter.get(sec_name, 0) + 1
        sub_idx = sub_chunk_counter[sec_name]

        # 同一章节拆成多块时必须带上 sub_idx，否则后写入的文件会覆盖前面的 chunk
        if sub_idx == 1:
            chunk_id = f"stories_{global_idx:04d}_{sec_idx:02d}"
        else:
            chunk_id = f"stories_{global_idx:04d}_{sec_idx:02d}_{sub_idx:02d}"

        chunks.append({
            "chunk_id": chunk_id,
            "section": sec_name,
            "content": content,
            "source_file": filename or ""
        })

def _process_knowledge_chunk(content: str, chunks: list, min_size: int, max_size: int, doc_title: str, sec_idx: int, sec_name: str, sub_chunk_counter: dict, chunk_prefix: str = "char_summary", source_file: str = "char_summary.md"):
    """实时处理单个 knowledge chunk：检查是否需要合并到前一块"""
    content = content.strip()
    if not content:
        return

    # 如果内容太大，先拆分
    if len(content) > max_size:
        paragraphs = content.split("\n\n")
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(para) > max_size:
                sentences = re.split(r'(?<=[。！？])\s*', para)
                for sent in sentences:
                    sent = sent.strip()
                    if not sent:
                        continue
                    if len(sent) > max_size:
                        # 句子仍然太长，按字符拆分
                        for i in range(0, len(sent), max_size):
                            sub_sent = sent[i:i+max_size].strip()
                            if sub_sent:
                                _process_knowledge_chunk(sub_sent, chunks, min_size, max_size, doc_title, sec_idx, sec_name, sub_chunk_counter, chunk_prefix, source_file)
                    else:
                        _process_knowledge_chunk(sent, chunks, min_size, max_size, doc_title, sec_idx, sec_name, sub_chunk_counter, chunk_prefix, source_file)
            else:
                _process_knowledge_chunk(para, chunks, min_size, max_size, doc_title, sec_idx, sec_name, sub_chunk_counter, chunk_prefix, source_file)
        return

    # 合并判断：
    # 1. 当前块或前一块 < min_size，且合并后 <= max_size，且累积 < target_size
    # 2. 或者当前块特别小（< 500字符），即使前一块超过target_size也允许合并
    should_merge = False
    if chunks:
        prev_size = len(chunks[-1]["content"])
        curr_size = len(content)
        target_size = CHUNK_CONFIG["target_size"]
        min_size = CHUNK_CONFIG["min_size"]

        # 条件1：常规合并
        condition1 = (prev_size < min_size or curr_size < min_size) and curr_size + prev_size + 2 <= max_size and prev_size < target_size
        # 条件2：特别小的块（< 500字符）强制合并
        condition2 = curr_size < 500 and curr_size + prev_size + 2 <= max_size

        should_merge = condition1 or condition2

    if should_merge:
        # 合并到前一个 chunk，避免重复标题
        if sec_name and sec_name != chunks[-1]["section"]:
            chunks[-1]["content"] += "\n\n## " + sec_name + "\n\n" + content
        else:
            chunks[-1]["content"] += "\n\n" + content
    else:
        # 新建 chunk，增加该章节的子块计数
        sub_chunk_counter[sec_name] = sub_chunk_counter.get(sec_name, 0) + 1
        sub_idx = sub_chunk_counter[sec_name]

        # 格式：prefix_01 或 prefix_01_02
        if sub_idx == 1:
            chunk_id = f"{chunk_prefix}_{sec_idx:02d}"
        else:
            chunk_id = f"{chunk_prefix}_{sec_idx:02d}_{sub_idx:02d}"

        chunks.append({
            "chunk_id": chunk_id,
            "section": sec_name,
            "content": content,
            "source_file": source_file
        })

def chunk_json_record(obj: Dict, collection: str, global_idx: int) -> List[Dict]:
    """Convert a JSON record to a single chunk per record.

    Each operator/enemy becomes one complete chunk, regardless of size.
    """
    name_field = "干员名" if "干员名" in obj else "名称"
    name = obj.get(name_field, obj.get("敌人索引", f"record_{global_idx}"))

    # Format the entire object as structured text
    lines = [f"# {name}"]

    for key, value in obj.items():
        if value is None or value == "" or value == [] or value == {}:
            continue

        if isinstance(value, dict):
            if not value:
                continue
            lines.append(f"## {key}")
            for sub_key, sub_value in value.items():
                if sub_value is None or sub_value == "":
                    continue
                lines.append(f"  {sub_key}: {sub_value}")
        elif isinstance(value, list):
            if not value:
                continue
            lines.append(f"## {key}")
            for item in value:
                if item is None or item == "":
                    continue
                if isinstance(item, dict):
                    item_str = ", ".join(f"{k}: {v}" for k, v in item.items() if v is not None and v != "")
                    if item_str:
                        lines.append(f"  - {item_str}")
                else:
                    lines.append(f"  - {item}")
        else:
            lines.append(f"## {key}: {value}")

    content = "\n".join(lines)

    # Single chunk for the entire record (no size limit)
    return [{
        "chunk_id": f"{collection}_{global_idx:04d}",
        "section": name,
        "content": content,
        "source_file": f"{collection}.json"
    }]

def chunk_memes(memes_data: Dict, game_terms_data: Dict = None) -> List[Dict]:
    """Chunk memes with improved strategy.

    Improvements:
    1. Merge all star nicknames into one chunk (often queried together)
    2. Split game terms into individual chunks (better for specific term lookup)
    3. Limit each chunk size to prevent information dilution
    """
    chunks = []

    # ========== 1. 游戏术语拆分为独立chunk ==========
    if game_terms_data:
        for idx, (term, meaning) in enumerate(sorted(game_terms_data.items()), 1):
            chunks.append({
                "chunk_id": f"memes_gameterm_{idx:03d}",
                "section": term,
                "content": f"## {term}\n\n{meaning}",
                "source_file": "明日方舟术语.json"
            })

    # ========== 2. 合并所有星级绰号到一个chunk ==========
    nicknames = []
    for star in ["六星", "五星", "四星", "三星", "二星", "一星"]:
        cat_key = f"{star}绰号_来源"
        if cat_key in memes_data and memes_data[cat_key]:
            data = memes_data[cat_key]
            if isinstance(data, dict):
                for k, v in data.items():
                    nicknames.append(f"{star} {k}: {v}")
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        for k, v in item.items():
                            nicknames.append(f"{star} {k}: {v}")
                    else:
                        nicknames.append(f"{star} {item}")

    if nicknames:
        chunks.append({
            "chunk_id": "memes_nicknames_001",
            "section": "干员绰号汇总",
            "content": "### 干员绰号汇总\n\n" + "\n".join(nicknames),
            "source_file": "arknights_memes_dataset.json"
        })

    # ========== 3. 其他梗分类 ==========
    meme_categories = [
        ("剧情梗_来源", "剧情梗"),
        ("抽卡梗_含义", "抽卡梗"),
        ("基建梗_含义", "基建梗"),
        ("活动梗_来源", "活动梗"),
        ("干员组合_成员_来源", "干员组合"),
        ("近期梗_来源", "近期梗"),
    ]

    for cat_key, cat_name in meme_categories:
        if cat_key in memes_data and memes_data[cat_key]:
            data = memes_data[cat_key]
            lines = [f"### {cat_name}"]

            if isinstance(data, dict):
                for k, v in data.items():
                    lines.append(f"## {k}: {v}")
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        for k, v in item.items():
                            lines.append(f"## {k}: {v}")
                    else:
                        lines.append(f"## {item}")

            content = "\n".join(lines)

            # If too large, split into smaller chunks
            if len(content) > CHUNK_CONFIG["max_chunk_size"] * 1.5:
                sub_items = []
                if isinstance(data, dict):
                    sub_items = list(data.items())
                elif isinstance(data, list):
                    sub_items = [(None, item) for item in data]

                sub_chunks_content = []
                current = lines[0]  # 标题

                for item in sub_items:
                    if isinstance(item, tuple):
                        line = f"## {item[0]}: {item[1]}" if item[0] else f"## {item[1]}"
                    else:
                        line = f"## {item}"

                    if len(current) + len(line) <= CHUNK_CONFIG["max_chunk_size"]:
                        current += "\n" + line
                    else:
                        sub_chunks_content.append(current)
                        current = lines[0] + "\n" + line

                if current:
                    sub_chunks_content.append(current)

                for sc_idx, sc in enumerate(sub_chunks_content, 1):
                    chunks.append({
                        "chunk_id": f"memes_{cat_key}_{sc_idx:02d}",
                        "section": cat_name,
                        "content": sc,
                        "source_file": "arknights_memes_dataset.json"
                    })
            else:
                chunks.append({
                    "chunk_id": f"memes_{cat_key}_001",
                    "section": cat_name,
                    "content": content,
                    "source_file": "arknights_memes_dataset.json"
                })

    return chunks

def chunk_operators_summary(summary: Dict) -> List[Dict]:
    """operators_summary is small (~3KB), return as 1 chunk."""
    lines = []
    for key, value in summary.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for k, v in value.items():
                lines.append(f"- {k}: {v}")
        elif isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, dict):
                    lines.append(f"- {', '.join(f'{kk}: {vv}' for kk, vv in item.items())}")
                else:
                    lines.append(f"- {item}")
        else:
            lines.append(f"{key}: {value}")
    return [{
        "chunk_id": "operators_summary_001",
        "section": "干员统计总览",
        "content": "\n".join(lines),
        "source_file": "operators_summary.json"
    }]

def chunk_enemies_summary(summary: Dict) -> List[Dict]:
    """enemies_summary is small, return as 1 chunk."""
    lines = []
    for key, value in summary.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for k, v in value.items():
                if isinstance(v, list):
                    lines.append(f"- {k}: {len(v)}个")
                else:
                    lines.append(f"- {k}: {v}")
        elif isinstance(value, list):
            lines.append(f"{key}: {len(value)}个")
        else:
            lines.append(f"{key}: {value}")
    return [{
        "chunk_id": "enemies_summary_001",
        "section": "敌人统计总览",
        "content": "\n".join(lines),
        "source_file": "enemies_summary.json"
    }]

def chunk_all_data(limit: int = None):
    """Main orchestrator: chunk all sources and write to chunks/ directory.

    Args:
        limit: if set, only chunk the first N files from operators/ and stories/
               (use for preview before full run).
    """
    chunks_dir = CHUNKS_DIR
    if chunks_dir.exists():
        for sub in ["operators", "stories", "knowledge"]:
            sub_path = chunks_dir / sub
            if sub_path.exists():
                shutil.rmtree(sub_path, ignore_errors=True)
            sub_path.mkdir(parents=True, exist_ok=True)
    else:
        chunks_dir.mkdir(parents=True, exist_ok=True)
        for sub in ["operators", "stories", "knowledge"]:
            (chunks_dir / sub).mkdir(parents=True, exist_ok=True)

    ops_files = sorted((DATA_DIR / 'operators').glob('*.md'))
    if limit is not None:
        ops_files = ops_files[:limit]
    for file_idx, filepath in enumerate(ops_files):
        content = load_md_file(str(filepath))
        # 提取干员名称（第一个 # 标题）
        first_line = content.split('\n')[0] if content else ""
        operator_name = first_line.lstrip('#').strip() if first_line.strip().startswith('#') else ""
        chunks = chunk_operators_file(content, filepath.name, file_idx + 1)
        for chunk in chunks:
            out_path = chunks_dir / "operators" / f"{chunk['chunk_id']}.md"
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(f"# {operator_name}\n\n# {chunk['section']}\n\n{chunk['content']}")

    story_files = sorted((DATA_DIR / 'stories').glob('*.md'))
    if limit is not None:
        story_files = story_files[:limit]
    for file_idx, filepath in enumerate(story_files):
        content = load_md_file(str(filepath))
        # 提取故事标题（第一个 # 标题）
        first_line = content.split('\n')[0] if content else ""
        story_title = first_line.lstrip('#').strip() if first_line.strip().startswith('#') else ""
        chunks = chunk_story_file(content, filepath.name, file_idx + 1)
        for chunk in chunks:
            out_path = chunks_dir / "stories" / f"{chunk['chunk_id']}.md"
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(f"# {story_title}\n\n# {chunk['section']}\n\n{chunk['content']}")

    ops = load_all_operators(str(DATA_DIR / 'all_operators.json'))
    for idx, op in enumerate(ops, 1):
        chunks = chunk_json_record(op, 'operators_json', idx)
        for chunk in chunks:
            out_path = chunks_dir / "knowledge" / f"{chunk['chunk_id']}.txt"
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(chunk['content'])

    enemies = load_all_enemies(str(DATA_DIR / 'all_enemies.json'))
    for idx, enemy in enumerate(enemies, 1):
        chunks = chunk_json_record(enemy, 'enemies_json', idx)
        for chunk in chunks:
            out_path = chunks_dir / "knowledge" / f"{chunk['chunk_id']}.txt"
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(chunk['content'])

    gameplay_content = load_gameplay(str(DATA_DIR / 'gameplay.md'))

    # ========== 改进后的gameplay切块逻辑 ==========
    # 1. 使用##作为分隔符，而非---
    # 2. 每个玩法作为一个完整chunk
    # 3. 避免标题重复

    # 首先跳过文件开头的元信息 (标题和介绍)
    lines = gameplay_content.split("\n")
    content_start_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("## "):
            content_start_idx = i
            break

    # 重新组装内容，跳过顶部的# 明日方舟玩法详解
    remaining_content = "\n".join(lines[content_start_idx:])

    # 按##分节 (每个玩法一个section)
    sections = split_by_sections(remaining_content, header_level=2)

    # 第一个section是玩法总览表格
    if sections and "总览" in sections[0][0]:
        # 提取表格内容作为第一个chunk
        _, table_content = sections[0]
        chunk_id = "gameplay_0001"
        out_path = chunks_dir / "knowledge" / f"{chunk_id}.md"
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(f"# 玩法总览\n\n{table_content.strip()}")
        sections = sections[1:]

    # 每个玩法一个chunk
    for idx, (sec_name, sec_content) in enumerate(sections, 1):
        if not sec_content.strip():
            continue

        # 清理内容，移除子标题
        sec_content = sec_content.strip()
        # 移除内容中的###子标题，只保留内容
        sec_content = re.sub(r'\n###\s+(.+?)\n', '\n', sec_content)

        chunk_id = f"gameplay_{idx+1:04d}"
        out_path = chunks_dir / "knowledge" / f"{chunk_id}.md"
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(f"# {sec_name}\n\n{sec_content}")

    ops_sum_path = DATA_DIR / 'operators_summary.json'
    if ops_sum_path.exists():
        ops_sum = load_operators_summary(str(ops_sum_path))
        summary_chunks = chunk_operators_summary(ops_sum)
        for chunk in summary_chunks:
            out_path = chunks_dir / "knowledge" / f"{chunk['chunk_id']}.txt"
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(chunk['content'])

    enemies_sum_path = DATA_DIR / 'enemies_summary.json'
    if enemies_sum_path.exists():
        enemies_sum = load_enemies_summary(str(enemies_sum_path))
        summary_chunks = chunk_enemies_summary(enemies_sum)
        for chunk in summary_chunks:
            out_path = chunks_dir / "knowledge" / f"{chunk['chunk_id']}.txt"
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(chunk['content'])

    memes_data = load_memes(str(DATA_DIR / 'arknights_memes_dataset.json'))
    memes_chunks = chunk_memes(memes_data)
    for chunk in memes_chunks:
        out_path = chunks_dir / "knowledge" / f"{chunk['chunk_id']}.txt"
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(chunk['content'])

    char_sum_path = DATA_DIR / "char_summary.md"
    if char_sum_path.exists():
        char_sum_content = load_md_file(str(char_sum_path))
        # 提取标题（第一个 # 标题）
        first_line = char_sum_content.split('\n')[0] if char_sum_content else ""
        doc_title = first_line.lstrip('#').strip() if first_line.strip().startswith('#') else "角色总表"

        # 使用与 operators/stories 相同的分节逻辑
        char_sections = split_by_sections(char_sum_content, header_level=2)
        char_sections = _promote_h1_title(char_sections)

        # 过滤空章节
        char_sections = [(n, s) for n, s in char_sections if s.strip()]

        min_size = CHUNK_CONFIG["min_size"]
        max_size = CHUNK_CONFIG["max_chunk_size"]

        chunks = []
        sub_chunk_counter = {}

        for i, (sec_name, sec_content) in enumerate(char_sections):
            if not sec_content.strip():
                continue

            sec_content = sec_content.strip()
            if sec_name not in sub_chunk_counter:
                sub_chunk_counter[sec_name] = 0

            # 章节 <= max_size：整体作为一个chunk
            if len(sec_content) <= max_size:
                _process_knowledge_chunk(sec_content, chunks, min_size, max_size, doc_title, i + 1, sec_name, sub_chunk_counter, chunk_prefix="char_summary", source_file="char_summary.md")
            else:
                # 章节 > max_size：按段落拆分
                paragraphs = sec_content.split("\n\n")
                for para in paragraphs:
                    para = para.strip()
                    if not para:
                        continue
                    if len(para) > max_size:
                        sentences = re.split(r'(?<=[。！？])\s*', para)
                        for sent in sentences:
                            sent = sent.strip()
                            if not sent:
                                continue
                            _process_knowledge_chunk(sent, chunks, min_size, max_size, doc_title, i + 1, sec_name, sub_chunk_counter, chunk_prefix="char_summary", source_file="char_summary.md")
                    else:
                        _process_knowledge_chunk(para, chunks, min_size, max_size, doc_title, i + 1, sec_name, sub_chunk_counter, chunk_prefix="char_summary", source_file="char_summary.md")

        for chunk in chunks:
            out_path = chunks_dir / "knowledge" / f"{chunk['chunk_id']}.txt"
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(f"# {doc_title}\n\n# {chunk['section']}\n\n{chunk['content']}")

    story_sum_path = DATA_DIR / "story_summary.md"
    if story_sum_path.exists():
        story_sum_content = load_md_file(str(story_sum_path))
        # 提取标题（第一个 # 标题）
        first_line = story_sum_content.split('\n')[0] if story_sum_content else ""
        doc_title = first_line.lstrip('#').strip() if first_line.strip().startswith('#') else "剧情总表"

        # 使用与 operators/stories 相同的分节逻辑
        story_sections = split_by_sections(story_sum_content, header_level=2)
        story_sections = _promote_h1_title(story_sections)

        # 过滤空章节
        story_sections = [(n, s) for n, s in story_sections if s.strip()]

        min_size = CHUNK_CONFIG["min_size"]
        max_size = CHUNK_CONFIG["max_chunk_size"]

        chunks = []
        sub_chunk_counter = {}

        for i, (sec_name, sec_content) in enumerate(story_sections):
            if not sec_content.strip():
                continue

            sec_content = sec_content.strip()
            if sec_name not in sub_chunk_counter:
                sub_chunk_counter[sec_name] = 0

            # 章节 <= max_size：整体作为一个chunk
            if len(sec_content) <= max_size:
                _process_knowledge_chunk(sec_content, chunks, min_size, max_size, doc_title, i + 1, sec_name, sub_chunk_counter, chunk_prefix="story_summary", source_file="story_summary.md")
            else:
                # 章节 > max_size：按段落拆分
                paragraphs = sec_content.split("\n\n")
                for para in paragraphs:
                    para = para.strip()
                    if not para:
                        continue
                    if len(para) > max_size:
                        sentences = re.split(r'(?<=[。！？])\s*', para)
                        for sent in sentences:
                            sent = sent.strip()
                            if not sent:
                                continue
                            _process_knowledge_chunk(sent, chunks, min_size, max_size, doc_title, i + 1, sec_name, sub_chunk_counter, chunk_prefix="story_summary", source_file="story_summary.md")
                    else:
                        _process_knowledge_chunk(para, chunks, min_size, max_size, doc_title, i + 1, sec_name, sub_chunk_counter, chunk_prefix="story_summary", source_file="story_summary.md")

        for chunk in chunks:
            out_path = chunks_dir / "knowledge" / f"{chunk['chunk_id']}.txt"
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(f"# {doc_title}\n\n# {chunk['section']}\n\n{chunk['content']}")

    print(f"Chunking complete!")
    print(f"  operators: {len(list((chunks_dir / 'operators').glob('*')))} files")
    print(f"  stories: {len(list((chunks_dir / 'stories').glob('*')))} files")
    print(f"  knowledge: {len(list((chunks_dir / 'knowledge').glob('*')))} files")

if __name__ == "__main__":
    chunk_all_data()
