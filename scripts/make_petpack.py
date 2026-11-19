# -*- coding: utf-8 -*-
"""
make_petpack.py —— 把 aboutme-pets 角色包转换为 dsh-pet pet pack（通用脱敏版）

用法:
    python make_petpack.py --pack grok [--pack claude ...] \
        --repo-root /path/to/aboutme-pets \
        --dst "$DSH_HOME/dsh-pet/pet"

依赖: Python 3.8+, Pillow, ffmpeg（在 PATH）。
说明:
  - 读取 aboutme-pets 的 pets/<pack>/actions.json 与 spritesheet/*.webp
  - 逐动作裁帧 -> libvpx-vp9 yuva420p (640x360, 12fps, alpha_mode=1) -> <dst>/<pack>-animation/*.webm
  - 生成 <dst>/<pack>-config.json：按分类语义把全部动作挂进 dsh-pet 动画池
    (idle/turn/drag/clicks/moves/categories/events/sleep)，缺省人设可后续自行修改
  - 幂等：已存在的 webm 跳过；网络失败自动回落 jsDelivr 镜像
许可: 脚本 MIT。**转换产出的素材遵循本仓库 CC BY-NC-SA 4.0 许可：禁止商用、公开使用须署名本仓库**
"""
import sys, os, json, argparse, urllib.request, urllib.parse, subprocess, shutil, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
FPS = 12
WORKERS = 4
ROOT = "https://raw.githubusercontent.com/hunmer/aboutme-pets/main/"
MIR = "https://cdn.jsdelivr.net/gh/hunmer/aboutme-pets@main/"

IDLE_ALIAS = {"待机", "小动作", "日常起居", "娱乐休闲", "起居"}

def cat_of(c):
    return "idle类" if c in IDLE_ALIAS else c

def fetch_bytes(rel):
    last = None
    for base in (MIR, ROOT):
        for _ in range(3):
            try:
                return urllib.request.urlopen(base + urllib.parse.quote(rel), timeout=120).read()
            except Exception as e:
                last = e; time.sleep(1.5)
    raise RuntimeError(f"fetch failed {rel}: {last}")

def process_clip(repo_root, pack, a, anim_dir, src_cache):
    aid = a["id"]
    dst = os.path.join(anim_dir, aid + ".webm")
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return ("skip", aid)
    sp = os.path.join(src_cache, a["sources"]["webp"].replace("/", os.sep))
    if not os.path.exists(sp) or os.path.getsize(sp) == 0:
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        local = os.path.join(repo_root, "pets", pack, a["sources"]["webp"].replace("/", os.sep))
        if os.path.exists(local):
            shutil.copyfile(local, sp)
        else:
            open(sp, "wb").write(fetch_bytes(f"pets/{pack}/" + a["sources"]["webp"]))
    fd = os.path.join(src_cache, f"frames-{pack}", aid)
    os.makedirs(fd, exist_ok=True)
    im = Image.open(sp).convert("RGBA")
    meta = a["spritesheet"]
    idx = 0
    for r in range(meta["rows"]):
        for c in range(meta["columns"]):
            if idx >= meta["frameCount"]: break
            im.crop((c*meta["frameWidth"], r*meta["frameHeight"],
                     (c+1)*meta["frameWidth"], (r+1)*meta["frameHeight"])).save(os.path.join(fd, f"{idx:04d}.png"))
            idx += 1
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(FPS), "-i", os.path.join(fd, "%04d.png"),
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
        "-b:v", "0", "-crf", "28", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-an", dst],
        capture_output=True, text=True)
    shutil.rmtree(fd, ignore_errors=True)
    return (("ok" if r.returncode == 0 else "fail"), aid)

def load_actions(repo_root, pack, src_cache):
    local = os.path.join(repo_root, "pets", pack, "actions.json")
    if os.path.exists(local):
        return json.load(open(local, encoding="utf-8"))["animations"]
    return json.loads(fetch_bytes(f"pets/{pack}/actions.json").decode("utf-8"))["animations"]

def build_pack(repo_root, pack, dst):
    act = load_actions(repo_root, pack, os.path.join(dst, ".cache"))
    catmap = {x["id"]: x.get("category", "?") for x in act}
    anim_dir = os.path.join(dst, pack + "-animation")
    os.makedirs(anim_dir, exist_ok=True)
    todo = [x for x in act if not os.path.exists(os.path.join(anim_dir, x["id"] + ".webm"))]
    print(f"[{pack}] total={len(act)} todo={len(todo)}")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for f in as_completed({ex.submit(process_clip, repo_root, pack, x, anim_dir, os.path.join(dst, ".cache")): x["id"] for x in todo}):
            st, aid = f.result()
            if st == "fail": print(f"  FAIL {aid}")

    def bycat(*names): return [x["id"] for x in act if catmap.get(x["id"]) in names]
    idle = bycat(*IDLE_ALIAS)
    cfg = {
        "whisperPrompt": "",          # 留空回落全局；想个性化自行填写
        "sleepStartTexts": ["我先睡一会儿。"], "sleepEndTexts": ["我醒啦。"],
        "sleepRefuseTexts": ["（装睡）……"],
        "pets": [{"id": pack + "1", "name": pack, "size": 462, "balanceEnabled": False,
                  "whisperEnabled": True, "workStatusEnabled": True, "fixedEnabled": False,
                  "display": "web",
                  "position": {"corner": "bottom-right", "marginX": 40, "marginY": 40}}],
        "animations": {
            "idle": idle,
            "turn": (["东张西望"] if "东张西望" in catmap else idle[:1]),
            "drag": bycat("拖拽"),
            "clicks": bycat("点击回应"),
            "moves": {"default": {"minDist": 60, "maxDist": 240, "margin": 20, "leadSec": 1, "tailSec": 1},
                      "actions": bycat("移动")},
            "categories": [{"id": g, "weight": 0, "actions": ids}
                           for g, ids in _groups(act, catmap).items()],
            "events": {"balance": sorted(bycat("余额")), "whisper": bycat("碎碎念", "特效"),
                       "workStatus": sorted(bycat("工作状态"))},
            "sleep": _sleep(act, catmap),
        },
        "animationWeights": {"idle": 10, "turn": 0, "move": 5},
    }
    # 分类权重按数量占比归一到 85（idle 10 + move 5）
    cats = cfg["animations"]["categories"]
    total_n = sum(len(c["actions"]) for c in cats) or 1
    acc = 0
    for i, c in enumerate(cats):
        c["weight"] = (85 - acc) if i == len(cats) - 1 else max(2, round(85 * len(c["actions"]) / total_n))
        acc += c["weight"]
    json.dump(cfg, open(os.path.join(dst, pack + "-config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"[{pack}] 完成: {len(os.listdir(os.path.join(dst, pack + '-animation')))} webm")

def _groups(act, catmap):
    groups = {}
    for x in act:
        g = cat_of(catmap.get(x["id"], "?"))
        if g == "idle类": g = "日常"
        groups.setdefault(g, [])
        if x["id"] not in groups[g]: groups[g].append(x["id"])
    return groups

def _sleep(act, catmap):
    def find(*keys):
        return next((x["id"] for x in act if any(k in x["id"] for k in keys)), None)
    start = find("打瞌睡"); loop = find("沉眠", "小憩"); end = find("哈欠")
    if start and loop and end:
        return [{"start": [start], "loop": [loop], "end": [end]}]
    return []

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", action="append", required=True, help="角色包名，可多次")
    ap.add_argument("--repo-root", default="", help="本地 clone 的 aboutme-pets 根目录（可选，缺省走网络）")
    ap.add_argument("--dst", required=True, help="dsh-pet 的 pet/ 目录（缺省 $DSH_HOME/dsh-pet/pet）")
    args = ap.parse_args()
    for pack in args.pack:
        build_pack(args.repo_root, pack, args.dst)
    print("ALL DONE")
