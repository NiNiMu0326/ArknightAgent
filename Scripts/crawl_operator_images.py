import requests
import json
import time
import re
import sys
from pathlib import Path
from multiprocessing import Pool, cpu_count

sys.path.insert(0, str(Path(__file__).parent))
from common import PRTS_HEADERS

API_URL = "https://prts.wiki/api.php"
HEADERS = PRTS_HEADERS

DATA_DIR = Path("data/operator_images")
INDEX_FILE = DATA_DIR / "index.json"

def get_image_url(filename):
    params = {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json"
    }
    try:
        response = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
        data = response.json()
        pages = data.get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            if "imageinfo" in page_data:
                return page_data["imageinfo"][0]["url"]
    except Exception:
        pass
    return None

def download_image(url, filepath):
    if not url:
        return False
    try:
        response = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        if response.status_code == 200:
            content_length = int(response.headers.get('content-length', 0))
            if content_length > 5000:
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True
    except Exception:
        pass
    return False

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
    r = requests.get(f"{API_URL}?action=query&list=categorymembers&cmtitle=Category:干员&cmlimit=500&format=json", headers=HEADERS, timeout=30)
    data = r.json()
    members = data.get("query", {}).get("categorymembers", [])
    operators = [m["title"] for m in members if not m["title"].startswith("Category:")]
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

    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(operator_data, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"爬取完成! 成功: {success_count}/{len(operators)}")
    print(f"索引文件: {INDEX_FILE}")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()
