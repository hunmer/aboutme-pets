#!/usr/bin/env python3
"""WebM → 透明 WebP 精灵图转换（convert_video_pet_spritesheet.ps1 的 Python 实现）。

只处理 WebM 源：枚举 --source-dir 下所有 *.webm（文件名去掉扩展名即动作 id），
为每个动作生成 spritesheet/<id>.webp 与同名 JSON，并把动作条目合并写回
--json-path（沿用 video pet manifest 结构）。目标 JSON 中已存在的动作只更新
sources.webp / spritesheet / audio，其余字段（name、category、idle、movement…）
原样保留；文件不存在时自动建最小骨架。
产物目录固定为 --json-path 所在目录下的 spritesheet/ 与 audio/。

与 PowerShell 版的差异只有实现语言和并行度：
  * 默认参数一致（640x360、12fps、8 列、1.5MB 上限、质量上限 100）；
  * ffmpeg / ffprobe / cwebp 由 PATH 自动定位，也可显式指定；
  * --jobs N 多进程并行（输出与串行完全一致，只是更快）；
  * 单个动作失败不会中断整批，结束时汇总并以非 0 退出码报告。

示例：
  python3 convert_video_pet_spritesheet.py \
    --source-dir webm --json-path pets/dsh/manifest.json --jobs 6
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

ALPHA_Q_TRIALS = (50, 25, 10, 1)


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def resolve_tools(cfg):
    """定位 ffmpeg / ffprobe / cwebp，缺失时给出可操作的报错。"""
    def locate(explicit, name, fallback_name=None):
        if explicit:
            if not os.path.isfile(explicit):
                raise SystemExit(f"Tool not found: {explicit}")
            return explicit
        found = shutil.which(name) or (fallback_name and shutil.which(fallback_name))
        if not found:
            raise SystemExit(
                f"Tool not found on PATH: {name}. "
                f"Install it or pass an explicit path (e.g. --ffmpeg /opt/homebrew/bin/{name})."
            )
        return found

    cfg = dict(cfg)
    cfg["ffmpeg"] = locate(cfg.get("ffmpeg"), "ffmpeg")
    cfg["ffprobe"] = locate(cfg.get("ffprobe") or os.path.join(
        os.path.dirname(cfg["ffmpeg"]), "ffprobe"), "ffprobe")
    cfg["cwebp"] = locate(cfg.get("cwebp"), "cwebp")
    return cfg


def probe_json(cfg, args):
    p = run([cfg["ffprobe"], "-v", "error", *args, "-of", "json"])
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {' '.join(args)}\n{p.stderr}")
    return json.loads(p.stdout or "{}")


# --------------------------------------------------------------------------- #
# 音频
# --------------------------------------------------------------------------- #
def probe_audio_codec(cfg, path):
    data = probe_json(cfg, ["-select_streams", "a:0", "-show_entries",
                            "stream=codec_name", path])
    streams = data.get("streams") or []
    if not streams:
        return None
    return streams[0].get("codec_name") or None


def ensure_audio_track(cfg, audio_dir, action_id, source):
    """提取首个音频流到 audio/<id>.m4a；源无音轨时保留已有的非空 m4a。

    静默源保留旧 m4a，是为了让「重新导出但没有自带音轨」的动作不丢音频能力；
    只有既无音频流、又无历史 m4a 时才返回 None。
    """
    out = os.path.join(audio_dir, f"{action_id}.m4a")
    codec = probe_audio_codec(cfg, source)
    if codec is None:
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return {
                "rel": f"audio/{action_id}.m4a",
                "codec": probe_audio_codec(cfg, out) or "aac",
                "streamCopy": False,
                "bytes": os.path.getsize(out),
                "retained": True,
            }
        return None

    stream_copy = codec == "aac"
    if cfg["resume"] and os.path.isfile(out) and os.path.getsize(out) > 0:
        return {
            "rel": f"audio/{action_id}.m4a",
            "codec": codec,
            "streamCopy": stream_copy,
            "bytes": os.path.getsize(out),
            "reused": True,
        }

    os.makedirs(audio_dir, exist_ok=True)
    encode_args = (["-c:a", "copy"] if stream_copy
                   else ["-c:a", "aac", "-b:a", "192k"])
    p = run([cfg["ffmpeg"], "-hide_banner", "-nostdin", "-y", "-v", "error",
             "-i", source, "-map", "0:a:0", "-vn", "-sn", "-dn",
             *encode_args, "-movflags", "+faststart", out])
    if p.returncode != 0:
        raise RuntimeError(f"Audio extraction failed: {source}\n{p.stderr}")
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise RuntimeError(f"Audio extraction produced no output: {out}")
    return {
        "rel": f"audio/{action_id}.m4a",
        "codec": codec,
        "streamCopy": stream_copy,
        "bytes": os.path.getsize(out),
        "reused": False,
    }


# --------------------------------------------------------------------------- #
# 视频 → 精灵图
# --------------------------------------------------------------------------- #
def count_frames(cfg, source, fps):
    """用 fps filter 跑一遍空输出，取最终 frame= 计数（与 PowerShell 版一致）。"""
    p = run([cfg["ffmpeg"], "-hide_banner", "-nostdin", "-v", "error", "-nostats",
             "-c:v", "libvpx-vp9", "-i", source, "-map", "0:v:0", "-an", "-sn",
             "-vf", f"fps={fps}", "-fps_mode", "passthrough",
             "-progress", "pipe:1", "-f", "null", "-"])
    if p.returncode != 0:
        raise RuntimeError(f"Frame counting failed: {source}\n{p.stderr}")
    lines = p.stdout.splitlines()
    frames = [line for line in lines if line.startswith("frame=")]
    if not frames or "progress=end" not in lines:
        raise RuntimeError(f"Missing completed FFmpeg progress: {source}")
    return int(frames[-1].split("=", 1)[1])


def build_sheet(cfg, source, rows, count, out_png):
    """把采样帧拼成 RGBA 大图，保留源 alpha 与透明留白。"""
    p = run([cfg["ffmpeg"], "-hide_banner", "-nostdin", "-y", "-v", "error",
             "-c:v", "libvpx-vp9", "-i", source, "-map", "0:v:0", "-an", "-sn",
             "-vf", (f"fps={cfg['fps']},"
                     f"scale={cfg['frame_width']}:{cfg['frame_height']}:flags=lanczos,"
                     f"format=rgba,"
                     f"tile=layout={cfg['columns']}x{rows}:nb_frames={count}:"
                     f"color=0x00000000"),
             "-frames:v", "1", "-update", "1", "-pix_fmt", "rgba", out_png])
    if p.returncode != 0:
        raise RuntimeError(f"Spritesheet conversion failed: {source}\n{p.stderr}")


def encode_webp(cfg, src_png, out_webp, candidate):
    """在字节上限内搜索最高 RGB 质量，alpha 质量按 50/25/10/1 退让。"""
    max_bytes = cfg["max_bytes"]
    best_quality, alpha_q = -1, 50
    base_quality = min(35, cfg["quality"])

    def attempt(quality, alpha_quality):
        p = run([cfg["cwebp"], "-quiet", "-m", "4", "-mt",
                 "-alpha_q", str(alpha_quality), "-q", str(quality),
                 src_png, "-o", candidate])
        if p.returncode != 0:
            raise RuntimeError(f"WebP encoding failed at q={quality}: {p.stderr}")
        return os.path.getsize(candidate)

    for trial_alpha in ALPHA_Q_TRIALS:
        if attempt(base_quality, trial_alpha) < max_bytes:
            shutil.copyfile(candidate, out_webp)
            best_quality, alpha_q = base_quality, trial_alpha
            break

    low = base_quality + 1 if best_quality >= 0 else 0
    high = cfg["quality"] if best_quality >= 0 else base_quality - 1
    if best_quality < 0:
        alpha_q = 1
    while low <= high:
        mid = (low + high) // 2
        if attempt(mid, alpha_q) < max_bytes:
            shutil.copyfile(candidate, out_webp)
            best_quality = mid
            low = mid + 1
        else:
            high = mid - 1

    if best_quality < 0:
        raise RuntimeError("Cannot fit sheet below byte limit without changing "
                           "dimensions or frames")
    return best_quality, alpha_q, os.path.getsize(out_webp)


def convert_one(task):
    """转换单个动作；返回 manifest 片段与日志行。"""
    source = task["source"]
    action_id = task["id"]
    # 逐动作的字节上限覆盖（例如 30 秒长的动作需要更大体积）；浅拷贝避免污染共享 cfg。
    cfg = dict(task["cfg"])
    cfg["max_bytes"] = cfg["bytes_overrides"].get(action_id, cfg["max_bytes"])
    workdir = tempfile.mkdtemp(prefix="aboutme-spritesheet-")
    try:
        audio = ensure_audio_track(cfg, cfg["audio_dir"], action_id, source)
        audio_log = ""
        if audio:
            note = (" (reused)" if audio.get("reused")
                    else " (retained: source silent)" if audio.get("retained") else "")
            audio_log = (f"[{action_id}] audio codec={audio['codec']} "
                         f"copy={audio['streamCopy']} bytes={audio['bytes']}{note}")

        out_webp = os.path.join(cfg["out_dir"], f"{action_id}.webp")
        meta_path = os.path.join(cfg["out_dir"], f"{action_id}.json")

        if cfg["resume"] and os.path.isfile(out_webp) and os.path.isfile(meta_path):
            with open(meta_path, encoding="utf-8") as fh:
                existing = json.load(fh)
            if (existing.get("FrameWidth") == cfg["frame_width"]
                    and existing.get("FrameHeight") == cfg["frame_height"]
                    and existing.get("SampleFps") == cfg["fps"]
                    and existing.get("Columns") == cfg["columns"]
                    and existing.get("MaxImageBytes") == cfg["max_bytes"]
                    and (existing.get("AlphaQuality") or 0) > 0
                    and existing.get("EncodedBytes") == os.path.getsize(out_webp)
                    and existing.get("EncodedBytes", cfg["max_bytes"]) < cfg["max_bytes"]):
                sheet = {"frameWidth": cfg["frame_width"], "frameHeight": cfg["frame_height"],
                         "columns": cfg["columns"], "rows": existing["Rows"],
                         "frameCount": existing["FrameCount"], "fps": cfg["fps"]}
                return {"id": action_id, "sheet": sheet, "audio": audio,
                        "audioLog": audio_log, "reused": True,
                        "log": f"[{action_id}] reused bytes={existing['EncodedBytes']}"}

        probe = probe_json(cfg, [
            "-select_streams", "v:0",
            "-show_entries", "format=duration:stream=r_frame_rate,pix_fmt:stream_tags=alpha_mode",
            source])
        duration = float(probe["format"]["duration"])
        num, den = probe["streams"][0]["r_frame_rate"].split("/")
        source_fps = float(num) / float(den)
        source_pix_fmt = probe["streams"][0].get("pix_fmt", "")
        tags = probe["streams"][0].get("tags") or {}
        # VP9 WebM 的 alpha 在 side data 里：ffprobe 仍报 yuv420p，只设 alpha_mode 标签，
        # 必须解码出 RGBA 平面才知道有没有透明。
        source_has_alpha = (
            any(token in source_pix_fmt
                for token in ("yuva", "rgba", "bgra", "argb", "abgr", "pal8"))
            or str(tags.get("alpha_mode")) == "1")

        frame_count = count_frames(cfg, source, cfg["fps"])
        if frame_count <= 0:
            raise RuntimeError(f"No sampled frames: {source}")
        rows = math.ceil(frame_count / cfg["columns"])

        sheet_png = os.path.join(workdir, "sheet.png")
        build_sheet(cfg, source, rows, frame_count, sheet_png)

        best_q, alpha_q, encoded = encode_webp(
            cfg, sheet_png, out_webp, os.path.join(workdir, "candidate.webp"))

        image = probe_json(cfg, ["-select_streams", "v:0",
                                 "-show_entries", "stream=width,height,pix_fmt",
                                 out_webp])["streams"][0]
        allowed = ({"rgba", "bgra", "yuva420p"} if source_has_alpha
                   else {"rgba", "bgra", "yuva420p", "yuv420p"})
        if (image["width"] != cfg["frame_width"] * cfg["columns"]
                or image["height"] != cfg["frame_height"] * rows
                or image.get("pix_fmt") not in allowed):
            raise RuntimeError(f"WebP dimensions or alpha format mismatch: {action_id}")

        sheet = {"frameWidth": cfg["frame_width"], "frameHeight": cfg["frame_height"],
                 "columns": cfg["columns"], "rows": rows,
                 "frameCount": frame_count, "fps": cfg["fps"]}
        meta = {
            "Source": os.path.basename(source),
            "FrameWidth": cfg["frame_width"],
            "FrameHeight": cfg["frame_height"],
            "Columns": cfg["columns"],
            "Rows": rows,
            "FrameCount": frame_count,
            "SampleFps": cfg["fps"],
            "SourceDurationSeconds": duration,
            "SourceFps": source_fps,
            "CanvasWidth": image["width"],
            "CanvasHeight": image["height"],
            "SourceVideoBytes": os.path.getsize(source),
            "SourcePixFmt": source_pix_fmt,
            "Alpha": ("RGBA" if source_has_alpha
                      else "Opaque (source has no alpha channel; "
                           "libwebp omits the alpha plane)"),
            "ImageFormat": "webp",
            "Lossless": False,
            "EncodingQuality": best_q,
            "AlphaQuality": alpha_q,
            "Encoder": "cwebp -m 4 -mt",
            "EncodedBytes": encoded,
            "MaxImageBytes": cfg["max_bytes"],
            "License": "Non-commercial; attribution required. "
                       "See NOTICE.txt and SOURCE-upstream.txt.",
        }
        if audio:
            meta.update({
                "HasAudio": True,
                "AudioFormat": "m4a",
                "AudioCodec": audio["codec"],
                "AudioStreamCopy": bool(audio["streamCopy"]),
                "AudioBytes": audio["bytes"],
            })
            if audio.get("retained"):
                meta["AudioNote"] = ("Source video has no audio stream; "
                                     "existing m4a from the same performance kept")
        else:
            meta["HasAudio"] = False

        tmp_meta = meta_path + ".tmp"
        with open(tmp_meta, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_meta, meta_path)

        return {
            "id": action_id, "sheet": sheet, "audio": audio, "reused": False,
            "audioLog": audio_log,
            "log": (f"[{action_id}] quality={best_q} frames={frame_count} "
                    f"grid={cfg['columns']}x{rows} texture={image['width']}x{image['height']} "
                    f"bytes={encoded} limit={cfg['max_bytes']}"),
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
def add_sorted(animations, entry):
    for index, item in enumerate(animations):
        if str(item.get("id")) > str(entry["id"]):
            animations.insert(index, entry)
            return
    animations.append(entry)


def load_manifest(json_path, cfg):
    if os.path.isfile(json_path):
        with open(json_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    else:
        package_name = os.path.basename(os.path.dirname(json_path))
        manifest = {
            "schemaVersion": 1,
            "id": package_name,
            "name": package_name,
            "canvasWidth": cfg["frame_width"],
            "canvasHeight": cfg["frame_height"],
            "animations": [],
            "metadata": {},
        }
    manifest.setdefault("animations", [])
    manifest.setdefault("metadata", {})
    return manifest


def apply_conversion_metadata(manifest, cfg):
    manifest["metadata"]["resourceFormat"] = (
        "Per-action lossy WebP spritesheets with alpha; optional AAC m4a audio track")
    manifest["metadata"]["sourceVideos"] = (
        "*.webm enumerated from --source-dir; filename without extension is the animation id")
    manifest["metadata"]["conversion"] = {
        "frameWidth": cfg["frame_width"],
        "frameHeight": cfg["frame_height"],
        "fps": cfg["fps"],
        "columns": cfg["columns"],
        "rows": "ceil(actual fps-filtered frame count / columns)",
        "frameCountMethod": "FFmpeg fps filter -> null output -> final progress frame count",
        "imageFormat": "webp",
        "lossless": False,
        "maxImageBytes": cfg["max_bytes"],
        "qualityCeiling": cfg["quality"],        "qualitySearch": ("Per-image RGB integer binary search; alpha quality 50/25/10/1 "
                          "as needed; encoded bytes strictly below maxImageBytes"),
        "encoder": "cwebp -m 4 -mt",
        "alphaHandling": ("Source alpha preserved via RGBA tiles; alpha-free sources encode "
                          "fully opaque and libwebp omits the alpha plane"),
        "audio": ("First audio stream extracted to audio/<id>.m4a (AAC; stream copy when the "
                  "source is already AAC, otherwise 192k re-encode); a silent source retains an "
                  "existing m4a from an earlier export of the same performance (json AudioNote); "
                  "no manifest audio reference only when neither exists"),
        "script": "convert_video_pet_spritesheet.py",
    }
    if cfg["bytes_overrides"]:
        manifest["metadata"]["conversion"]["maxImageBytesOverrides"] = dict(
            sorted(cfg["bytes_overrides"].items()))
        manifest["metadata"]["conversion"]["maxImageBytesNote"] = (
            "maxImageBytes is the default; actions listed in maxImageBytesOverrides use "
            "their own (longer source clips keep more frames and therefore need more bytes)")


def write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="WebM -> 透明 WebP 精灵图 + m4a 音频 + manifest 合并")
    parser.add_argument("--source-dir", required=True,
                        help="包含 *.webm 的目录；文件名去扩展名即动作 id")
    parser.add_argument("--json-path", required=True,
                        help="目标 manifest.json 路径；产物写到其同级的 spritesheet/ 与 audio/")
    parser.add_argument("--resume", action="store_true",
                        help="跳过已生成且参数一致的动作，仅补缺失项")
    parser.add_argument("--frame-width", type=int, default=640)
    parser.add_argument("--frame-height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--columns", type=int, default=8)
    parser.add_argument("--max-bytes", type=int, default=1_500_000,
                        help="单张 WebP 的字节上限（默认 1.5MB，与 PowerShell 版一致）")
    parser.add_argument("--bytes-override", action="append", default=[],
                        metavar="ID=BYTES",
                        help="为单个动作覆盖字节上限，可重复（如 30 秒长动作 工作加班=3000000）")
    parser.add_argument("--quality", type=int, default=100, help="cwebp RGB 质量上限")
    parser.add_argument("--jobs", type=int, default=4, help="并行进程数（默认 4）")
    parser.add_argument("--ffmpeg", help="ffmpeg 可执行文件路径（默认从 PATH 查找）")
    parser.add_argument("--ffprobe", help="ffprobe 可执行文件路径（默认随 ffmpeg）")
    parser.add_argument("--cwebp", help="cwebp 可执行文件路径（默认从 PATH 查找）")
    return parser.parse_args(argv)


def parse_bytes_overrides(values):
    """把 ["id=bytes", ...] 解析成 {id: bytes}；格式错误直接报错。"""
    overrides = {}
    for raw in values:
        action_id, _, size = raw.rpartition("=")
        if not action_id or not size.strip().isdigit() or int(size) <= 0:
            raise SystemExit(f"Bad --bytes-override (expected ID=BYTES): {raw}")
        overrides[action_id] = int(size)
    return overrides


def main(argv=None):
    args = parse_args(argv)
    cfg = {
        "frame_width": args.frame_width,
        "frame_height": args.frame_height,
        "fps": args.fps,
        "columns": args.columns,
        "max_bytes": args.max_bytes,
        "quality": args.quality,
        "resume": args.resume,
        "bytes_overrides": parse_bytes_overrides(args.bytes_override),
        "ffmpeg": args.ffmpeg,
        "ffprobe": args.ffprobe,
        "cwebp": args.cwebp,
    }
    cfg = resolve_tools(cfg)

    source_dir = os.path.abspath(args.source_dir)
    json_path = os.path.abspath(args.json_path)
    package_dir = os.path.dirname(json_path)
    cfg["out_dir"] = os.path.join(package_dir, "spritesheet")
    cfg["audio_dir"] = os.path.join(package_dir, "audio")
    os.makedirs(cfg["out_dir"], exist_ok=True)

    files = sorted(name for name in os.listdir(source_dir)
                   if name.lower().endswith(".webm"))
    if not files:
        raise SystemExit(f"No .webm files found in: {source_dir}")
    known = {name[:-len(".webm")] for name in files}
    for action_id in cfg["bytes_overrides"]:
        if action_id not in known:
            print(f"Warning: --bytes-override for unknown action: {action_id}",
                  file=sys.stderr)

    manifest = load_manifest(json_path, cfg)
    tasks = [{"cfg": cfg, "source": os.path.join(source_dir, name),
              "id": name[:-len(".webm")]} for name in files]

    results, failures = [], []
    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(convert_one, task): task for task in tasks}
        for done, future in enumerate(_as_completed(futures), 1):
            task = futures[future]
            try:
                result = future.result()
            except Exception as exc:                      # noqa: BLE001 - 逐条汇报
                failures.append((task["id"], exc))
                print(f"[{done}/{len(tasks)}] [{task['id']}] FAILED: {exc}", flush=True)
                continue
            results.append(result)
            if result["audioLog"]:
                print(result["audioLog"], flush=True)
            print(f"[{done}/{len(tasks)}] {result['log']}", flush=True)

    animations = list(manifest["animations"])
    for result in results:
        target = next((item for item in animations
                       if str(item.get("id")) == result["id"]), None)
        if target is None:
            target = {"id": result["id"], "name": result["id"],
                      "category": "未分类", "sources": {}, "spritesheet": None}
            add_sorted(animations, target)
        target["sources"] = {"webp": f"spritesheet/{result['id']}.webp"}
        target["spritesheet"] = result["sheet"]
        if result["audio"]:
            target["audio"] = result["audio"]["rel"]
        else:
            target.pop("audio", None)

    manifest["animations"] = animations
    apply_conversion_metadata(manifest, cfg)
    write_json(json_path, manifest)

    webps = [name for name in os.listdir(cfg["out_dir"]) if name.endswith(".webp")]
    sizes = [os.path.getsize(os.path.join(cfg["out_dir"], name)) for name in webps]
    audio_count = sum(1 for result in results if result["audio"])
    print(f"Completed {len(results)} actions. WebP count={len(webps)}, "
          f"WebP bytes={sum(sizes)}, max bytes={max(sizes, default=0)}.")
    print(f"Audio count={audio_count}.")
    if failures:
        print(f"Failed {len(failures)} action(s):", file=sys.stderr)
        for action_id, exc in failures:
            print(f"  {action_id}: {exc}", file=sys.stderr)
        return 1
    return 0


def _as_completed(futures):
    from concurrent.futures import as_completed
    return as_completed(futures)


if __name__ == "__main__":
    sys.exit(main())
