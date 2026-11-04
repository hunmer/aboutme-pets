"""Transparent Video Maker — external tool discovery and child-process environment."""
import os
import shutil
import sys


def find_rembg():
    """Locate the rembg CLI and make it reachable via PATH.

    Lookup order: $REMBG_EXE -> PATH -> a .venv next to this package
    -> $REMBG_HOME -> ~/.local/bin -> the macOS per-user Python bin dir.
    Returns the executable path, or None when rembg is not installed.
    """
    exe = os.environ.get("REMBG_EXE", "").strip()
    if exe and os.path.isfile(exe):
        return exe
    found = shutil.which("rembg")
    if found:
        return found
    here = os.path.dirname(os.path.abspath(__file__))
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    cands = [
        os.environ.get("REMBG_HOME", "").strip(),
        os.path.join(here, ".venv", "bin"),
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser(os.path.join("~/Library/Python", py, "bin")),
    ]
    for d in cands:
        if not d:
            continue
        p = os.path.join(d, "rembg")
        if os.path.isfile(p) and os.access(p, os.X_OK):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            return p
    return None


REMBG_EXE = find_rembg()

# Video extensions recognised by the GUI (kept in sync with the open-file dialog).
VIDEO_EXTS = (".mp4", ".mov", ".avi", ".webm", ".mkv")


def find_videos(folder, recursive=False):
    """Video files in `folder`, sorted by name (walks subfolders when asked)."""
    if recursive:
        out = []
        for root, dirs, files in os.walk(folder):
            dirs.sort()
            for f in sorted(files):
                if f.lower().endswith(VIDEO_EXTS):
                    out.append(os.path.join(root, f))
        return out
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in sorted(names)
            if n.lower().endswith(VIDEO_EXTS)
            and os.path.isfile(os.path.join(folder, n))]


def output_path(outdir, video, fmt):
    """Output file for one pipeline run — the single naming convention."""
    base = os.path.splitext(os.path.basename(video))[0]
    return os.path.join(outdir, f"{base}_transparent.{fmt}")


def plan_batch(videos, outdir, fmt, skip_existing=False):
    """[(video, outfile), ...]; drops clips whose output already exists."""
    plan = []
    for v in videos:
        out = output_path(outdir, v, fmt)
        if skip_existing and os.path.exists(out):
            continue
        plan.append((v, out))
    return plan


def clean_env():
    """Environment dict for child processes.

    Collapse case-duplicate `NO_PROXY` entries and drop bracketed IPv6 hosts:
    httpx 0.28 (imported by gradio inside rembg) raises `Invalid port: ':1]'`
    on entries such as `[::1]`.
    """
    env = dict(os.environ)
    for key in [k for k in env if k.upper() == "NO_PROXY"]:
        parts = [p.strip() for p in env[key].split(",") if p.strip()]
        env["NO_PROXY"] = ",".join(p for p in parts if "[" not in p and "]" not in p)
        if key != "NO_PROXY":
            del env[key]
    return env
