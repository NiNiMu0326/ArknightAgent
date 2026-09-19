import requests
import json
import os
import time
import re
import sys
from pathlib import Path
from multiprocessing import Pool, cpu_count

sys.path.insert(0, str(Path(__file__).parent))
from common import PRTS_HEADERS, fetch_category_members

API_URL = "https://prts.wiki/api.php"
HEADERS = PRTS_HEADERS

DATA_DIR = Path("data/operator_images")
INDEX_FILE = DATA_DIR / "index.json"

def log_failure(action, target, reason):
    """失败不再静默吞掉：输出 URL 与原因，便于区分『资源不存在』和『请求失败』。"""
    print(f"[FAIL] {action} {target} -> {reason}", file=sys.stderr, flush=True)

def parse_content_length(value):
    """content-length 解析失败返回 None（分块传输本来就没有该响应头）。"""
    try:
        expected = int(value)
    except (TypeError, ValueError):
        return None
    return expected if expected >= 0 else None

def remove_partial(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass

def get_image_url(filename):
    params = {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json"
    }
    target = f"{API_URL}?titles=File:{filename}"
    try:
        response = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
        if response.status_code != 200:
            log_failure("查询", target, f"HTTP {response.status_code}")
            return None
        data = response.json()
    except Exception as e:
        log_failure("查询", target, f"{type(e).__name__}: {e}")
        return None

    pages = data.get("query", {}).get("pages", {})
    for page_data in pages.values():
        # imageinfo 可能缺失、为空列表或不含 url（文件不存在），不能直接下标取值
        imageinfo = page_data.get("imageinfo") if isinstance(page_data, dict) else None
        if not isinstance(imageinfo, list) or not imageinfo:
            continue
        first = imageinfo[0]
        url = first.get("url") if isinstance(first, dict) else None
        if url:
            return url
    return None

def download_image(url, filepath):
    """以 HTTP 状态码判定成功，并校验实际写入字节数。

    content-length 不再当成功门槛（分块传输没有该头、小于 5KB 的合法图片会被误杀），
    只在存在时用于比对是否被截断；先写 .part 再 os.replace，失败不留半截文件。
    """
    if not url:
        return False
    filepath = Path(filepath)
    tmp_path = filepath.with_name(filepath.name + ".part")
    expected = None
    written = 0
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        # stream=True 的响应必须用 with 关闭，否则异常路径下连接不会及时归还连接池
        with requests.get(url, headers=HEADERS, timeout=60, stream=True) as response:
            if response.status_code != 200:
                log_failure("下载", url, f"HTTP {response.status_code}")
                return False
            expected = parse_content_length(response.headers.get("content-length"))
            with open(tmp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    f.write(chunk)
                    written += len(chunk)
    except Exception as e:
        log_failure("下载", url, f"{type(e).__name__}: {e}")
        remove_partial(tmp_path)
        return False

    if written == 0:
        log_failure("下载", url, "响应体为空")
        remove_partial(tmp_path)
        return False
    if expected is not None and written != expected:
        log_failure("下载", url, f"字节数不一致：期望 {expected}，实际 {written}（响应被截断）")
        remove_partial(tmp_path)
        return False

    try:
        os.replace(tmp_path, filepath)  # 原子替换，避免残留半截图片
    except OSError as e:
        log_failure("下载", url, f"替换文件失败: {e}")
        remove_partial(tmp_path)
        return False
    return True

def sanitize(name):
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def process_operator(operator):
    safe_name = sanitize(operator)
    result = {
        "name": operator,
        "avatar": None,
        "portrait": None,
        "elite_1": None,
        "elite_2": None,
        "skins": []
    }

    avatar_file = f"头像 {operator}.png"
    avatar_url = get_image_url(avatar_file)
    if avatar_url:
        path = DATA_DIR / "avatars" / f"头像_{safe_name}.png"
        if download_image(avatar_url, path):
            result["avatar"] = f"avatars/头像_{safe_name}.png"

    portrait_file = f"立绘 {operator} 1.png"
    portrait_url = get_image_url(portrait_file)
    if portrait_url:
        path = DATA_DIR / "portraits" / f"立绘_{safe_name}.png"
        if download_image(portrait_url, path):
            result["portrait"] = f"portraits/立绘_{safe_name}.png"

    elite_1_file = f"立绘 {operator} 1+.png"
    elite_1_url = get_image_url(elite_1_file)
    if elite_1_url:
        path = DATA_DIR / "elite_1" / f"立绘_{safe_name}_精英1.png"
        if download_image(elite_1_url, path):
            result["elite_1"] = f"elite_1/立绘_{safe_name}_精英1.png"

    elite_2_file = f"立绘 {operator} 2.png"
    elite_2_url = get_image_url(elite_2_file)
    if elite_2_url:
        path = DATA_DIR / "elite_2" / f"立绘_{safe_name}_精英2.png"
        if download_image(elite_2_url, path):
            result["elite_2"] = f"elite_2/立绘_{safe_name}_精英2.png"

    skin_count = 0
    for j in range(1, 15):
        skin_file = f"立绘 {operator} skin{j}.png"
        skin_url = get_image_url(skin_file)
        if skin_url:
            path = DATA_DIR / "skins" / f"立绘_{safe_name}_皮肤{j}.png"
            if download_image(skin_url, path):
                result["skins"].append(f"skins/立绘_{safe_name}_皮肤{j}.png")
                skin_count += 1

    time.sleep(0.1)
    return (operator, result, skin_count)

def main():
    print("=" * 50)
    print("PRTS Wiki 干员立绘爬虫 (多进程版)")
    print("=" * 50)

    for d in ["avatars", "portraits", "elite_1", "elite_2", "skins"]:
        (DATA_DIR / d).mkdir(parents=True, exist_ok=True)
    print("目录结构已创建")

    print("获取干员列表...")
    members = fetch_category_members("Category:干员", headers=HEADERS)
    operators = [m for m in members if not m.startswith("Category:")]
    print(f"共有 {len(operators)} 个干员")

    num_workers = min(cpu_count(), 8)
    print(f"使用 {num_workers} 个进程并行处理")

    operator_data = {}
    success_count = 0

    with Pool(num_workers) as pool:
        for i, (operator, result, skin_count) in enumerate(pool.imap_unordered(process_operator, operators)):
            if result["avatar"] or result["portrait"]:
                operator_data[operator] = result
                success_count += 1
                status = "✓"
            else:
                status = "✗"

            skin_str = f" 皮肤{skin_count}" if skin_count > 0 else ""
            print(f"[{i+1}/{len(operators)}] {status} {operator}{skin_str}")

    # 先写 .part 再原子替换：中途失败不会破坏已有的 index.json
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_index = INDEX_FILE.with_name(INDEX_FILE.name + ".part")
    with open(tmp_index, 'w', encoding='utf-8') as f:
        json.dump(operator_data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_index, INDEX_FILE)

    print(f"\n{'='*50}")
    print(f"爬取完成! 成功: {success_count}/{len(operators)}")
    print(f"索引文件: {INDEX_FILE}")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()
