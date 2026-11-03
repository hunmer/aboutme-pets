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
