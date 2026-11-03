#!/usr/bin/env python3
"""生成预览清单 preview.json，并把清单同步内联进 preview.html。

清单结构（网页与第三方工具都可直接读）：
  schemaVersion / generatedAt / pets[]
    pets[]: id、name、cover、idleAnimationId、actionsFile、totalActions、categories[]
      categories[]: name、count、actions[]
        actions[]: id、name、category、webp、frameWidth、frameHeight、columns、rows、
                   frameCount、fps、audio

动作数据来自 pets/<id>/actions.json（回退 manifest.json），路径一律写成仓库根目录相对路径，
因此 preview.html 需与 preview.json 放在仓库根目录。
"""

import datetime
import json
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
INVENTORY_FILE = os.path.join(ROOT, "preview.json")
HTML_FILE = os.path.join(ROOT, "preview.html")
START = "<!-- preview-inventory:start -->"
END = "<!-- preview-inventory:end -->"

# 分类展示顺序：先按语义分组，未列出的按动作数量降序排在后面。
CATEGORY_ORDER = [
    "日常起居", "吃喝", "运动健身", "才艺表演", "手工节日",
    "工作职业", "娱乐休闲", "情绪反应", "换装造型",
    "待机", "移动", "转向", "玩耍", "吃什么", "工作状态",
    "点击回应", "碎碎念", "文字", "时节", "余额", "小动作", "拖拽", "未分类",
]


def load_actions(pet_dir):
    actions_path = os.path.join(pet_dir, "actions.json")
    manifest_path = os.path.join(pet_dir, "manifest.json")
    for path in (actions_path, manifest_path):
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)["animations"], os.path.relpath(path, ROOT)
    raise SystemExit(f"No actions.json/manifest.json in: {pet_dir}")


def build_action(entry, base_rel):
    """base_rel 为包目录（相对仓库根目录）；actions.json 里的资源路径都相对包目录。"""
    sheet = entry["spritesheet"]
    webp = (entry.get("sources") or {}).get("webp")
    if not webp:
        return None
    webp = os.path.normpath(os.path.join(base_rel, webp))
    if not os.path.isfile(os.path.join(ROOT, webp)):
        return None
    audio = entry.get("audio")
    if audio:
        audio = os.path.normpath(os.path.join(base_rel, audio))
        if not os.path.isfile(os.path.join(ROOT, audio)):
            audio = None
    return {
        "id": entry["id"],
        "name": entry.get("name") or entry["id"],
        "category": entry.get("category") or "未分类",
        "webp": webp,
        "frameWidth": sheet["frameWidth"],
        "frameHeight": sheet["frameHeight"],
        "columns": sheet["columns"],
        "rows": sheet["rows"],
        "frameCount": sheet["frameCount"],
        "fps": sheet.get("fps", 12),
        "audio": audio,
    }


def build_pet(pet):
    base_rel = os.path.dirname(pet["manifest"])
    pet_dir = os.path.join(ROOT, base_rel)
    animations, actions_file = load_actions(pet_dir)
    actions = [a for a in (build_action(entry, base_rel) for entry in animations) if a]

    buckets = {}
    for action in actions:
        buckets.setdefault(action["category"], []).append(action)

    def order(name):
        if name in CATEGORY_ORDER:
            return (0, CATEGORY_ORDER.index(name), name)
        return (1, -len(buckets[name]), name)

    categories = [
        {"name": name, "count": len(buckets[name]), "actions": buckets[name]}
        for name in sorted(buckets, key=order)
    ]
    return {
        "id": pet["id"],
        "name": pet["name"],
        "description": pet.get("description", ""),
        "cover": pet.get("cover"),
        "idleAnimationId": None,
        "actionsFile": actions_file,
        "totalActions": len(actions),
        "categories": categories,
    }


def main():
    with open(os.path.join(ROOT, "pets.json"), encoding="utf-8") as fh:
        catalog = json.load(fh)

    pets = []
    for pet in catalog["pets"]:
        built = build_pet(pet)
        manifest_path = os.path.join(ROOT, pet["manifest"])
        if os.path.isfile(manifest_path):
            with open(manifest_path, encoding="utf-8") as fh:
                built["idleAnimationId"] = json.load(fh).get("idleAnimationId")
        pets.append(built)

    inventory = {
        "schemaVersion": 1,
        "generatedAt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "pets.json -> pets/<id>/actions.json + spritesheet/*.webp",
        "canvas": {"width": 640, "height": 360, "fps": 12, "columns": 8},
        "pets": pets,
    }

    text = json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    with open(INVENTORY_FILE, "w", encoding="utf-8") as fh:
        fh.write(text)

    if os.path.isfile(HTML_FILE):
        with open(HTML_FILE, encoding="utf-8") as fh:
            html = fh.read()
        block = (f'{START}\n<script type="application/json" id="inventory-data">\n'
                 f'{text}</script>\n{END}')
        html, count = re.subn(re.escape(START) + r".*?" + re.escape(END), lambda _: block,
                              html, flags=re.S)
        with open(HTML_FILE, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"inline sync: {'ok' if count else 'markers not found'}")

    total = sum(p["totalActions"] for p in pets)
    print(f"wrote {os.path.relpath(INVENTORY_FILE, ROOT)}: {len(pets)} pets, {total} actions")


if __name__ == "__main__":
    main()
