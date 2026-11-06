#!/usr/bin/env python3
"""透明 WebP 精灵图 → 透明 WebM 反向转换（convert_video_pet_spritesheet.py 的逆操作）。

读取 --input-dir 下所有 spritesheet 精灵图：
  * <id>.webp  拼贴精灵图（FrameWidth x FrameHeight 网格，Columns 列）
  * <id>.json  同名元数据（FrameWidth/FrameHeight/Columns/FrameCount/SampleFps）
按元数据把大图逐帧 crop 出来，重编码为带 alpha 的 VP9 WebM（yuva420p，
alpha_mode=1），输出到 --output-dir/<id>.webm。可选把 --input-dir 同级
（或 --audio-dir）下 audio/<id>.m4a 混流进视频。

示例：
  python3 spritesheet_to_video_pet.py --input-dir pets/dsh/spritesheet \
    --output-dir webm-restored --jobs 4
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def resolve_tools(cfg):
    """定位 ffmpeg，缺失时给出可操作的报错。"""
    explicit = cfg.get("ffmpeg")
    if explicit:
        if not os.path.isfile(explicit):
            raise SystemExit(f"Tool not found: {explicit}")
        return explicit
    found = shutil.which("ffmpeg")
    if not found:
        raise SystemExit(
            "Tool not found on PATH: ffmpeg. "
            "Install it or pass an explicit path (e.g. --ffmpeg /opt/homebrew/bin/ffmpeg)."
        )
    return found


def find_audio(input_dir, audio_dir, action_id):
    """查找可选的 audio/<id>.m4a（显式 --audio-dir 优先，其次 input-dir 同级）。"""
    candidates = [
        os.path.join(audio_dir, f"{action_id}.m4a") if audio_dir else None,
        os.path.join(os.path.dirname(os.path.abspath(input_dir)), "audio", f"{action_id}.m4a"),
        os.path.join(input_dir, "..", "audio", f"{action_id}.m4a"),
        os.path.join(input_dir, "audio", f"{action_id}.m4a"),
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.path.getsize(path) > 0:
            return os.path.abspath(path)
    return None


def restore_one(task):
    """把一张精灵图还原成透明 WebM；返回日志行。"""
    action_id = task["id"]
    cfg = dict(task["cfg"])
    webp_path = os.path.join(cfg["input_dir"], f"{action_id}.webp")
    meta_path = os.path.join(cfg["input_dir"], f"{action_id}.json")

    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)

    fw, fh_ = int(meta["FrameWidth"]), int(meta["FrameHeight"])
    columns = int(meta["Columns"])
    frame_count = int(meta["FrameCount"])
    fps = int(meta.get("SampleFps") or cfg["fps"])
    rows = int(meta.get("Rows") or math.ceil(frame_count / columns))
    if frame_count <= 0 or columns <= 0:
        raise RuntimeError(f"Invalid metadata: {meta_path}")

    out_path = os.path.join(cfg["output_dir"], f"{action_id}.webm")
    if cfg["resume"] and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        return {"id": action_id, "log": f"[{action_id}] reused bytes={os.path.getsize(out_path)}"}

    audio_path = find_audio(cfg["input_dir"], cfg["audio_dir"], action_id)

    # 核心：单帧大图 → loop 复制 N 帧 → crop 按帧号走网格 → yuva420p VP9。
    # crop 的 x/y 表达式按帧求值，n 从 0 开始：x=(n mod cols)*fw, y=(n/cols)*fh。
    vf = (
        f"crop={fw}:{fh_}:x='mod(n\\,{columns})*{fw}':y='floor(n/{columns})*{fh_}',"
        f"format=yuva420p"
    )
    cmd = [
        cfg["ffmpeg"], "-hide_banner", "-nostdin", "-y", "-v", "error",
        "-loop", "1", "-framerate", str(fps), "-i", webp_path,
        "-frames:v", str(frame_count),
        "-vf", vf,
        "-fps_mode", "passthrough",
        "-c:v", "libvpx-vp9", "-auto-alt-ref", "0",
        "-pix_fmt", "yuva420p",
        "-row-mt", "1",
        "-b:v", "0", "-crf", str(cfg["crf"]),
        "-alpha_mode", "1",
    ]
    if audio_path:
        cmd += ["-i", audio_path, "-map", "0:v:0", "-map", "1:a:0",
                "-c:a", "copy", "-shortest"]
    cmd += ["-metadata:s:v:0", "alpha_mode=1", out_path]

    workdir = tempfile.mkdtemp(prefix="aboutme-restore-")
    try:
        p = run(cmd, cwd=workdir)
        if p.returncode != 0 and "-alpha_mode" in " ".join(cmd):
            # 旧版 ffmpeg 不支持 -alpha_mode 输出选项：去掉后重试（靠 metadata 标签）。
            retry = [a for i, a in enumerate(cmd) if not (
                a == "-alpha_mode" or (i and cmd[i - 1] == "-alpha_mode"))]
            p = run(retry, cwd=workdir)
        if p.returncode != 0:
            raise RuntimeError(f"WebM encoding failed: {webp_path}\n{p.stderr}")
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(f"WebM encoding produced no output: {out_path}")
        bytes_ = os.path.getsize(out_path)
        note = f" +audio={os.path.basename(audio_path)}" if audio_path else ""
        return {"id": action_id,
                "log": (f"[{action_id}] frames={frame_count} grid={columns}x{rows} "
                        f"frame={fw}x{fh_} fps={fps} crf={cfg['crf']} "
                        f"bytes={bytes_}{note}")}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="透明 WebP 精灵图 + JSON 元数据 -> 透明 WebM")
    parser.add_argument("--input-dir", required=True,
                        help="包含 <id>.webp 与 <id>.json 的目录（如 spritesheet/）")
    parser.add_argument("--output-dir", required=True,
                        help="WebM 输出目录，不存在则创建")
    parser.add_argument("--audio-dir",
                        help="可选的 audio/ 目录（默认自动探测 input-dir 同级）")
    parser.add_argument("--resume", action="store_true",
                        help="跳过已存在的输出 WebM")
    parser.add_argument("--fps", type=int, default=12,
                        help="元数据缺 SampleFps 时的回退帧率（默认 12）")
    parser.add_argument("--crf", type=int, default=20,
                        help="VP9 CRF 质量（默认 20，越小质量越高）")
    parser.add_argument("--jobs", type=int, default=4, help="并行进程数（默认 4）")
    parser.add_argument("--ffmpeg", help="ffmpeg 可执行文件路径（默认从 PATH 查找）")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    input_dir = os.path.abspath(args.input_dir)
    output_dir = os.path.abspath(args.output_dir)
    if not os.path.isdir(input_dir):
        raise SystemExit(f"Input directory not found: {input_dir}")
    os.makedirs(output_dir, exist_ok=True)

    cfg = {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "audio_dir": os.path.abspath(args.audio_dir) if args.audio_dir else None,
        "resume": args.resume,
        "fps": args.fps,
        "crf": args.crf,
        "ffmpeg": resolve_tools({"ffmpeg": args.ffmpeg}),
    }

    ids = sorted(name[:-len(".webp")] for name in os.listdir(input_dir)
                 if name.lower().endswith(".webp"))
    ids = [i for i in ids if os.path.isfile(os.path.join(input_dir, f"{i}.json"))]
    if not ids:
        raise SystemExit(f"No paired .webp + .json found in: {input_dir}")

    tasks = [{"cfg": cfg, "id": action_id} for action_id in ids]
    failures = []
    done_count = 0
    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(restore_one, t): t for t in tasks}
        from concurrent.futures import as_completed
        for future in as_completed(futures):
            task = futures[future]
            done_count += 1
            try:
                result = future.result()
            except Exception as exc:                  # noqa: BLE001 - 逐条汇报
                failures.append((task["id"], exc))
                print(f"[{done_count}/{len(tasks)}] [{task['id']}] FAILED: {exc}",
                      flush=True)
                continue
            print(f"[{done_count}/{len(tasks)}] {result['log']}", flush=True)

    webms = [name for name in os.listdir(output_dir) if name.endswith(".webm")]
    sizes = [os.path.getsize(os.path.join(output_dir, name)) for name in webms]
    print(f"Completed {len(tasks) - len(failures)} actions. WebM count={len(webms)}, "
          f"WebM bytes={sum(sizes)}.")
    if failures:
        print(f"Failed {len(failures)} action(s):", file=sys.stderr)
        for action_id, exc in failures:
            print(f"  {action_id}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
