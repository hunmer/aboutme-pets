# -*- coding: utf-8 -*-
"""Rename pet videos and batch-extract audio.

1. In each source folder, strip the leading index code from filenames:
     1-02 原地小憩沉眠.mp4  ->  原地小憩沉眠.mp4   (claude)
     001-原地小憩沉眠.mp4   ->  原地小憩沉眠.mp4   (gemini)
     S001 待机呼吸休闲.mp4  ->  待机呼吸休闲.mp4   (qwen)
2. Extract the audio track of every video with ffmpeg into
     pets/<pet>/audio/<name>.m4a   (AAC, mirroring pets/dsh1/audio)

Re-runnable: renames are skipped when already clean, audio extraction is
skipped when the .m4a already exists. Videos without an audio stream are
reported and skipped.
"""
import json
import os
import re
import subprocess
import sys

ROOT = r"D:\aboutme-pets"
SOURCES = {
    "claude": r"C:\Users\Administrator\Documents\claude",
    "gemini": r"C:\Users\Administrator\Documents\gemini",
    "qwen": r"C:\Users\Administrator\Documents\qwen",
}
# leading code: optional letters, digits, optional "-digits", then space or dash
CODE_RE = re.compile(r"^[A-Za-z]?\d+(?:-\d+)?[\s_-]+")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def has_audio(path):
    p = run(["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path])
    return "audio" in p.stdout


def main():
    for pet, src in SOURCES.items():
        if not os.path.isdir(src):
            print(f"[{pet}] source folder missing: {src}")
            continue
        audios = os.path.join(ROOT, "pets", pet, "audio")
        os.makedirs(audios, exist_ok=True)
        print(f"\n== {pet}: {src}")

        videos = sorted(f for f in os.listdir(src) if f.lower().endswith(".mp4"))
        for f in videos:
            base = os.path.splitext(f)[0]
            clean = CODE_RE.sub("", base).strip() or base
            cur = os.path.join(src, f)
            if clean != base:
                dst = os.path.join(src, clean + ".mp4")
                if os.path.exists(dst):
                    print(f"  !! rename collision, kept: {f} -> {clean}.mp4 "
                          f"(target exists)")
                    name = base  # extract under original name
                else:
                    os.rename(cur, dst)
                    print(f"  renamed: {f} -> {clean}.mp4")
                    name = clean
            else:
                name = base  # already clean

            out = os.path.join(audios, f"{name}.m4a")
            if os.path.exists(out):
                continue
            src_now = os.path.join(src, (name if name != base else f) + ".mp4")
            if not has_audio(src_now):
                print(f"  .. no audio stream: {name}")
                continue
            p = run(["ffmpeg", "-y", "-v", "error", "-i", src_now,
                     "-vn", "-c:a", "aac", "-b:a", "128k", out])
            if p.returncode == 0:
                print(f"  audio -> pets/{pet}/audio/{name}.m4a")
            else:
                print(f"  !! ffmpeg failed on {name}: {p.stderr.strip()[:200]}")
    print("\ndone.")


if __name__ == "__main__":
    sys.exit(main())
