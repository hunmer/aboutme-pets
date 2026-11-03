"""Terminal interface with a live progress bar — no Tk, works over SSH.

    python3 TransparentVideoMaker.py video -i clip.mp4 --method chroma
    python3 TransparentVideoMaker.py frames -i frames/ --fps 30

The GUI is still what you get with no arguments at all.
"""
import argparse
import collections
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time

from .encode import encode_cmd
from .tools import clean_env, find_rembg


class CliError(Exception):
    """A user-facing failure: printed as `error: …` with exit status 2."""


# ── Terminal progress bar ─────────────────────────────────────────────────
def _hms(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Bar:
    """Single-line progress bar on stderr; silently inert when not a TTY."""

    WIDTH = 24
    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label, total=None, unit="f", stream=None, enabled=None):
        self.label = label
        self.total = float(total) if total else None
        self.unit = unit
        self.stream = stream if stream is not None else sys.stderr
        if enabled is None:
            enabled = self.stream.isatty()
        self.enabled = enabled
        self.value = 0.0
        self.speed = ""
        self.note = ""
        self.started = time.monotonic()
        self._last = 0.0

    def update(self, value=None, note=None, force=False):
        if value is not None:
            self.value = float(value)
        if note is not None:
            self.note = note
        if self.enabled:
            self._render(force=force)

    def finish(self):
        if self.enabled:
            self.note = "done"
            self._render(force=True)
            self.stream.write("\n")
            self.stream.flush()

    def fail(self):
        if self.enabled:
            self.note = "failed"
            self._render(force=True)
            self.stream.write("\n")
            self.stream.flush()

    def _render(self, force=False):
        now = time.monotonic()
        if not force and now - self._last < 0.06:
            return
        self._last = now
        cols = max(shutil.get_terminal_size((80, 24)).columns, 20)
        elapsed = max(now - self.started, 1e-9)
        head = f"{self.label:<17.17}"
        note = f"  {self.note}" if self.note else ""

        def room(tail):
            return cols - 1 - len(head) - len(tail) - 3  # " [" + "]" + slack

        def bar_width(tail):
            return max(8, min(self.WIDTH, room(tail)))

        def fit(variants):
            """Use the richest variant that still leaves room for a bar."""
            for cand in variants:
                if room(cand) >= 10:
                    return cand
            return variants[-1]

        if self.total:
            frac = min(max(self.value / self.total, 0.0), 1.0)
            rate = self.value / elapsed
            if self.unit == "s":
                counts = f"{self.value:.1f}/{self.total:.1f}s"
                speed = f"{rate:.1f}x" if rate else "-"
            else:
                counts = f"{self.value:.0f}/{self.total:.0f}f"
                speed = self.speed or (f"{rate:.0f}f/s" if rate else "-")
            if self.value >= self.total:
                eta = "00:00"
            elif rate > 0:
                eta = _hms((self.total - self.value) / rate)
            else:
                eta = "--:--"
            pct = f"{frac * 100:3.0f}%"
            tail = fit([f" {pct}  {counts}  {speed}  ETA {eta}{note}",
                        f" {pct}  {counts}  {speed}{note}",
                        f" {pct}  {counts}{note}",
                        f" {pct}{note}"])
            width = bar_width(tail)
            filled = int(round(frac * width))
            bar = "█" * filled + "░" * (width - filled)
            line = f"{head} [{bar}]{tail}"
        else:
            spin = self.SPIN[int(now * 8) % len(self.SPIN)]
            tail = fit([f"  {self.value:.0f}{self.unit}  {_hms(elapsed)}{note}",
                        f"  {self.value:.0f}{self.unit}{note}"])
            width = bar_width(tail)
            line = f"{head} [{spin}{' ' * (width - 1)}]{tail}"

        if len(line) >= cols:
            line = line[: cols - 1]
        self.stream.write("\r" + line + "\x1b[K")
        self.stream.flush()


def say(msg, quiet=False):
    if not quiet:
        print(msg, file=sys.stderr, flush=True)


def _drain(stream, sink):
    try:
        for line in stream:
            sink.append(line.rstrip())
    except Exception:
        pass


# ── Probing ───────────────────────────────────────────────────────────────
def _ffprobe(argv):
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", *argv],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              env=clean_env(), timeout=30)
    except FileNotFoundError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def probe_duration(path):
    out = _ffprobe(["-show_entries", "format=duration",
                    "-of", "default=nw=1:nk=1", path])
    try:
        return float(out)
    except (TypeError, ValueError):
        return None


def probe_fps(path):
    out = _ffprobe(["-select_streams", "v:0",
                    "-show_entries", "stream=avg_frame_rate",
                    "-of", "default=nw=1:nk=1", path])
    m = re.match(r"^(\d+)/(\d+)$", out or "")
    if m and int(m.group(2)):
        fps = int(m.group(1)) / int(m.group(2))
        return round(fps, 3) if fps else None
    try:
        return float(out) or None
    except (TypeError, ValueError):
        return None


def _pattern_regex(pattern):
    """Translate an ffmpeg image pattern such as `frame%04d.png` to a regex."""
    if "%" not in pattern:
        return None
    head, _, tail = pattern.partition("%")
    m = re.match(r"^0?(\d*)d(.*)$", tail)
    if not m:
        return None
    width, rest = m.group(1), m.group(2)
    digits = r"\d{%s}" % width if width else r"\d+"
    return re.compile("^" + re.escape(head) + digits + re.escape(rest) + "$")


def count_matching(folder, pattern):
    if not os.path.isdir(folder):
        return 0
    rx = _pattern_regex(pattern)
    if rx is None:
        return 0
    return sum(1 for name in os.listdir(folder) if rx.match(name))


def count_pngs(folder):
    if not os.path.isdir(folder):
        return 0
    return sum(1 for name in os.listdir(folder) if name.lower().endswith(".png"))


def detect_pattern(folder):
    names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".png"))
    if not names:
        raise CliError(f"no PNG frames in {folder}")
    sample = names[0]
    m_std = re.match(r"^([a-zA-Z_\-]+)(\d+)\.png$", sample)
    if m_std:
        return f"{m_std.group(1)}%0{len(m_std.group(2))}d.png"
    if re.match(r"^(.+?)\s*\((\d+)\)\.png$", sample):
        raise CliError(
            f"copy-style filenames such as {sample!r} cannot be read by ffmpeg — "
            "rename them to frame0001.png, frame0002.png … (or pass --pattern)")
    raise CliError(f"cannot auto-detect a pattern from {sample!r} — pass --pattern")


def normalize_color(value):
    c = value.strip()
    if c.startswith("#"):
        c = c[1:]
    elif c[:2].lower() == "0x":
        c = c[2:]
    if re.fullmatch(r"[0-9a-fA-F]{6}", c) or re.fullmatch(r"[0-9a-fA-F]{8}", c):
        return "0x" + c.upper()
    if re.fullmatch(r"[A-Za-z]+", c):
        return c
    raise CliError(f"invalid colour {value!r} — use #00FF00 or a name like green")


# ── Runners ───────────────────────────────────────────────────────────────
def run_ffmpeg(cmd, total=None, unit="f", label="ffmpeg", quiet=False,
               dry_run=False):
    """Run ffmpeg with `-progress pipe:1` and render a bar from its keys."""
    cmd = [cmd[0], "-hide_banner", "-nostdin", "-nostats",
           "-progress", "pipe:1", *cmd[1:]]
    if dry_run:
        say("    $ " + " ".join(shlex.quote(c) for c in cmd), quiet)
        return True

    bar = Bar(label, total, unit, enabled=not quiet)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace",
                                env=clean_env())
    except FileNotFoundError:
        raise CliError("ffmpeg not found — brew install ffmpeg")

    errs = collections.deque(maxlen=40)
    threading.Thread(target=_drain, args=(proc.stderr, errs), daemon=True).start()
    try:
        for line in proc.stdout:
            key, _, val = line.strip().partition("=")
            val = val.strip()
            if key == "frame":
                try:
                    frame = int(val)
                except ValueError:
                    continue
                if unit == "f":
                    bar.update(frame)
            elif key == "out_time_us" and unit == "s":
                try:
                    bar.update(int(val) / 1_000_000)
                except ValueError:
                    pass
            elif key == "speed":
                bar.speed = val
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        raise

    if proc.returncode == 0:
        bar.finish()
        return True
    bar.fail()
    tail = "\n".join(list(errs)[-12:]).strip()
    if tail:
        print(tail, file=sys.stderr, flush=True)
    return False


def run_rembg(cmd, total=None, label="rembg", quiet=False, dry_run=False):
    """Run the rembg CLI, polling its output folder for progress."""
    if dry_run:
        say("    $ " + " ".join(shlex.quote(c) for c in cmd), quiet)
        return True

    outdir = cmd[-1]
    bar = Bar(label, total, "f", enabled=not quiet)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace",
                                env=clean_env())
    except FileNotFoundError:
        raise CliError("rembg not found — python3 -m pip install 'rembg[cli]'")

    lines = collections.deque(maxlen=40)
    threading.Thread(target=_drain, args=(proc.stdout, lines), daemon=True).start()
    try:
        while proc.poll() is None:
            bar.update(count_pngs(outdir))
            time.sleep(0.2)
    except KeyboardInterrupt:
        proc.terminate()
        raise

    if proc.returncode == 0:
        bar.update(count_pngs(outdir), force=True)
        bar.finish()
        return True
    bar.fail()
    tail = "\n".join(list(lines)[-12:]).strip()
    if tail:
        print(tail, file=sys.stderr, flush=True)
    return False


# ── Helpers ───────────────────────────────────────────────────────────────
def _resolve_output(user_path, src, suffix):
    if not user_path:
        out = os.path.join(os.path.dirname(src),
                           os.path.splitext(os.path.basename(src))[0] + suffix)
    else:
        out = os.path.abspath(os.path.expanduser(user_path))
        if os.path.isdir(out) or user_path.endswith(os.sep):
            out = os.path.join(
                out, os.path.splitext(os.path.basename(src))[0] + suffix)
    parent = os.path.dirname(out) or "."
    os.makedirs(parent, exist_ok=True)
    return out


def _num(value):
    return f"{value:g}"


def _size_label(nbytes):
    return (f"{nbytes / 2**20:.2f} MB" if nbytes >= 2**20
            else f"{nbytes / 1024:.0f} KB")


_SIZE_RE = re.compile(r"^([0-9]*\.?[0-9]+)\s*([kmg]?)i?b?$")


def parse_size(text):
    """'1.5M', '1500k', '12MB', '800000' → bytes (powers of 1024)."""
    m = _SIZE_RE.match(str(text).strip().lower().replace("_", ""))
    if not m:
        raise CliError(f"cannot read a size from {text!r} — try 1.5M or 1500k")
    value, unit = float(m.group(1)), m.group(2)
    return int(value * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[unit])


def sample_color(path, timeout=120):
    """Average colour of the first frame's top-left corner → 0xRRGGBB, or None.

    AI-generated "green screens" are rarely pure #00FF00, so `--color auto`
    reads the actual backdrop colour from the clip instead of guessing.
    """
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1",
           "-vf", "crop=64:64:0:0,scale=1:1:flags=area",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              env=clean_env())
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or len(proc.stdout) < 3:
        return None
    r, g, b = proc.stdout[0], proc.stdout[1], proc.stdout[2]
    return f"0x{r:02X}{g:02X}{b:02X}"


def _target_bitrate(target, duration):
    """bits/s that makes colour + alpha streams land near *target* bytes.

    VP9 stores alpha as a second stream that shares the `-b:v` budget, so the
    delivered file is about 1.8× the bitrate alone would give.
    """
    if duration and duration > 0:
        return max(60_000, int(target * 8 / duration / 2.0))
    return 400_000


def _check_range(flag, value, lo, hi):
    if value is not None and not (lo <= value <= hi):
        raise CliError(f"{flag} must be between {lo:g} and {hi:g} "
                       f"(got {value:g})")


def _check_common(a):
    _check_range("--crf", a.crf, 0, 63)
    if a.fps is not None and a.fps <= 0:
        raise CliError(f"--fps must be > 0 (got {a.fps:g})")


# ── Commands ──────────────────────────────────────────────────────────────
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv", ".flv",
              ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".3gp"}


def _find_videos(folder, recursive=False):
    """Video files directly under *folder*, or anywhere below it."""
    found = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in files:
                stem, ext = os.path.splitext(name)
                if (name.startswith(".") or ext.lower() not in VIDEO_EXTS
                        or stem.endswith("_transparent")):
                    continue
                found.append(os.path.join(root, name))
    else:
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            stem, ext = os.path.splitext(name)
            if (not os.path.isfile(path) or name.startswith(".")
                    or ext.lower() not in VIDEO_EXTS
                    or stem.endswith("_transparent")):
                continue
            found.append(path)
    return found


def _inside(path, folder):
    try:
        return os.path.commonpath([os.path.abspath(path),
                                   os.path.abspath(folder)]) == os.path.abspath(folder)
    except ValueError:  # different drives on Windows
        return False


def _process_video(src, out, a, quiet, dry, tag):
    """Run the three steps for one video; raise CliError on the first failure."""
    base = tag
    work = (os.path.abspath(os.path.expanduser(a.workdir)) if a.workdir
            else os.path.dirname(out))
    frames = os.path.join(work, f"{base}_frames")
    transp = os.path.join(work, f"{base}_transparent")

    def _run():
        if not dry:
            os.makedirs(work, exist_ok=True)
            shutil.rmtree(frames, ignore_errors=True)
            shutil.rmtree(transp, ignore_errors=True)
            os.makedirs(frames, exist_ok=True)

        say(f"\n▶ 1/3  extracting frames  →  {frames}", quiet)
        duration = probe_duration(src)
        ok = run_ffmpeg(["ffmpeg", "-y", "-i", src, "-pix_fmt", "rgba",
                         os.path.join(frames, "frame%04d.png")],
                        total=duration, unit="s", label="extract frames",
                        quiet=quiet, dry_run=dry)
        if not ok:
            raise CliError("frame extraction failed")

        total = count_matching(frames, "frame%04d.png")
        if not dry:
            say(f"    {total} frames extracted", quiet)

        if a.method == "chroma":
            color = a.color
            if color == "auto":
                color = sample_color(src) or "#00ff00"
                say(f"    sampled background colour: {color}", quiet)
            color = normalize_color(color)
            vf = f"chromakey={color}:{round(a.similarity, 3)}:{round(a.blend, 3)}"
            say(f"\n▶ 2/3  chroma key  ({color}, similarity "
                f"{a.similarity:g}, blend {a.blend:g})", quiet)
            if not dry:
                os.makedirs(transp, exist_ok=True)
            ok = run_ffmpeg(["ffmpeg", "-y",
                             "-i", os.path.join(frames, "frame%04d.png"),
                             "-vf", vf, "-pix_fmt", "yuva420p",
                             os.path.join(transp, "frame%04d.png")],
                            total=total, unit="f", label="chroma key",
                            quiet=quiet, dry_run=dry)
            if not ok:
                raise CliError("chroma key removal failed")
        else:
            exe = find_rembg()
            if not exe and not dry:
                raise CliError("rembg not found — python3 -m pip install "
                               "'rembg[cli]' or set REMBG_EXE=/path/to/rembg")
            cmd = [exe or "rembg", "p"]
            if a.model != "u2net":
                cmd += ["-m", a.model]
            cmd += [frames, transp]
            say(f"\n▶ 2/3  AI removal  ({a.model})", quiet)
            if not dry:
                os.makedirs(transp, exist_ok=True)
            ok = run_rembg(cmd, total=total, label=f"rembg {a.model}",
                           quiet=quiet, dry_run=dry)
            if not ok:
                raise CliError("AI background removal failed")

        fmt = a.format
        fps = a.fps or probe_fps(src) or 30.0
        target = parse_size(a.target_size) if getattr(a, "target_size", None) else None
        if target and fmt != "webm":
            raise CliError("--target-size only works with --format webm")
        if target and not dry and not duration:
            raise CliError("cannot size the output: unknown duration")

        def encode(*args, **kwargs):
            ok = run_ffmpeg(*args, **kwargs)
            if not ok:
                raise CliError("encoding failed")

        def encode_two_pass(bitrate):
            log = os.path.join(work, f"{base}_vp9pass")
            say(f"\n▶ 3/3  encoding {fmt}  ({_num(fps)} fps, two-pass "
                f"{bitrate / 1000:.0f} kbps → ~{target / 2**20:.2f} MB)  →  {out}",
                quiet)
            for pass_no in (1, 2):
                encode(encode_cmd(transp, _num(fps), a.crf, fmt, out,
                                  bitrate=bitrate, pass_no=pass_no,
                                  passlog=log),
                       total=total, unit="f", label=f"encode {fmt} p{pass_no}",
                       quiet=quiet, dry_run=dry)
            if not dry:
                for name in os.listdir(work):
                    if name.startswith(f"{base}_vp9pass"):
                        os.unlink(os.path.join(work, name))

        if target:
            bitrate = _target_bitrate(target, duration)
            encode_two_pass(bitrate)
            size = os.path.getsize(out) if not dry else 0
            if size > target:  # keep the promised cap: one cheaper retry
                retry = max(60_000, int(bitrate * target / size * 0.92))
                say(f"    {_size_label(size)} > {_size_label(target)}, "
                    f"re-encoding at {retry / 1000:.0f} kbps", quiet)
                encode_two_pass(retry)
                size = os.path.getsize(out)
                if size > target:
                    say(f"    warning: still {_size_label(size)}", quiet)
        else:
            say(f"\n▶ 3/3  encoding {fmt}  ({_num(fps)} fps, crf {a.crf})  →  {out}",
                quiet)
            encode(encode_cmd(transp, _num(fps), a.crf, fmt, out),
                   total=total, unit="f", label=f"encode {fmt}",
                   quiet=quiet, dry_run=dry)

        if not dry and not a.keep_frames:
            shutil.rmtree(frames, ignore_errors=True)
            shutil.rmtree(transp, ignore_errors=True)
            say("    temp frame folders removed  (--keep-frames to keep them)", quiet)

        if not dry:
            say(f"\n✓  {out}", quiet)

    try:
        _run()
    except CliError:
        if not dry and not a.keep_frames:
            shutil.rmtree(frames, ignore_errors=True)
            shutil.rmtree(transp, ignore_errors=True)
        raise


def _batch_parallel(todo, a, folder, n):
    """Convert *todo* items (i, src, out, tag) with `--jobs` worker threads."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    done, failed = [], []
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        futures = {pool.submit(_process_video, src, out, a, True, a.dry_run, tag):
                   (i, src, out) for i, src, out, tag in todo}
        for fut in as_completed(futures):
            i, src, out = futures[fut]
            rel = os.path.relpath(src, folder)
            try:
                fut.result()
            except CliError as exc:
                failed.append((src, str(exc)))
                print(f"✗ [{i}/{n}] {rel}: {exc}", file=sys.stderr, flush=True)
            else:
                done.append(out)
                size = os.path.getsize(out) if os.path.exists(out) else 0
                if not a.quiet:
                    print(f"✓ [{i}/{n}] {rel}  {_size_label(size)}", flush=True)
    return done, failed


def _cmd_video_batch(folder, a):
    """`video -i SOME/FOLDER`: convert every video inside, keep going on errors."""
    quiet, dry = a.quiet, a.dry_run
    outdir = (os.path.abspath(os.path.expanduser(a.output)) if a.output
              else folder)
    if os.path.isfile(outdir):
        raise CliError("--output must be a folder when --input is a folder "
                       f"(got {outdir})")
    videos = _find_videos(folder, a.recursive)
    if os.path.abspath(outdir) != folder:
        videos = [v for v in videos if not _inside(v, outdir)]
    if not videos:
        hint = "" if a.recursive else "  (add --recursive to include subfolders)"
        raise CliError(f"no video files found in {folder}{hint}")
    if not dry:
        os.makedirs(outdir, exist_ok=True)

    n = len(videos)
    say(f"\n▶ batch  {n} video{'s' if n != 1 else ''}  →  {outdir}", quiet)
    done, failed, skipped, todo = [], [], [], []
    started = time.monotonic()

    for i, src in enumerate(videos, 1):
        base = os.path.splitext(os.path.basename(src))[0]
        out = os.path.join(outdir, f"{base}_transparent.{a.format}")
        if os.path.exists(out) and not a.overwrite:
            say(f"[{i}/{n}] {os.path.relpath(src, folder)}\n"
                f"    skipped, output exists: {out}  (pass --overwrite)", quiet)
            skipped.append(src)
            continue
        todo.append((i, src, out, f"{i:02d}_{base}"))

    jobs = max(1, getattr(a, "jobs", 1) or 1)
    if jobs > 1 and not dry and len(todo) > 1:
        say(f"    {min(jobs, len(todo))} workers in parallel", quiet)
        done, failed = _batch_parallel(todo, a, folder, n)
    else:
        for i, src, out, tag in todo:
            rel = os.path.relpath(src, folder)
            say(f"\n[{i}/{n}] {rel}", quiet)
            try:
                _process_video(src, out, a, quiet, dry, tag)
            except CliError as exc:
                failed.append((src, str(exc)))
                print(f"error: {rel}: {exc}", file=sys.stderr, flush=True)
                continue
            done.append(out)
            if not dry:
                print(out, flush=True)

    took = time.monotonic() - started
    parts = [f"{len(done)} done"]
    if skipped:
        parts.append(f"{len(skipped)} skipped")
    if failed:
        parts.append(f"{len(failed)} failed")
    mark = "✗" if failed else "✓"
    say(f"\n{mark}  {', '.join(parts)}  ({_hms(took)})", quiet)
    return 1 if failed else 0


def cmd_video(a):
    src = os.path.abspath(os.path.expanduser(a.input))
    if shutil.which("ffmpeg") is None:
        raise CliError("ffmpeg not found — brew install ffmpeg")
    _check_common(a)
    _check_range("--similarity", a.similarity, 0.0, 1.0)
    _check_range("--blend", a.blend, 0.0, 1.0)
    if getattr(a, "jobs", 1) < 1:
        raise CliError(f"--jobs must be at least 1 (got {a.jobs})")
    if a.target_size:
        parse_size(a.target_size)  # fail early on a typo, not after step 1
    if a.method == "ai" and not a.dry_run and find_rembg() is None:
        raise CliError("rembg not found — python3 -m pip install 'rembg[cli]'\n"
                       "         or set REMBG_EXE=/path/to/rembg")

    if os.path.isdir(src):
        return _cmd_video_batch(src, a)
    if not os.path.isfile(src):
        raise CliError(f"input video not found: {src}")

    out = _resolve_output(a.output, src, f"_transparent.{a.format}")
    if os.path.exists(out) and not a.overwrite and not a.dry_run:
        raise CliError(f"output exists: {out}  (pass --overwrite to replace)")

    base = os.path.splitext(os.path.basename(src))[0]
    _process_video(src, out, a, a.quiet, a.dry_run, base)
    print(out)
    return 0


def cmd_frames(a):
    folder = os.path.abspath(os.path.expanduser(a.input))
    if not os.path.isdir(folder):
        raise CliError(f"frame folder not found: {folder}")
    if shutil.which("ffmpeg") is None:
        raise CliError("ffmpeg not found — brew install ffmpeg")

    _check_common(a)
    pattern = a.pattern or detect_pattern(folder)
    if "%" not in pattern:
        raise CliError(f"pattern needs a %d field, got {pattern!r}")
    total = count_matching(folder, pattern)
    if total == 0:
        raise CliError(f"no files in {folder} match {pattern!r}")

    fmt = a.format
    name = os.path.basename(folder.rstrip(os.sep)) or "frames"
    out = (os.path.abspath(os.path.expanduser(a.output)) if a.output
           else os.path.join(os.path.dirname(folder), f"{name}_video.{fmt}"))
    if os.path.isdir(out):
        out = os.path.join(out, f"{name}_video.{fmt}")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    if os.path.exists(out) and not a.overwrite and not a.dry_run:
        raise CliError(f"output exists: {out}  (pass --overwrite to replace)")

    say(f"\n▶  encoding {total} frames  ({pattern}, {_num(a.fps)} fps, "
        f"crf {a.crf}, {fmt})  →  {out}", a.quiet)
    ok = run_ffmpeg(encode_cmd(folder, _num(a.fps), a.crf, fmt, out,
                               pattern=pattern),
                    total=total, unit="f", label=f"encode {fmt}",
                    quiet=a.quiet, dry_run=a.dry_run)
    if not ok:
        raise CliError("encoding failed")
    if not a.dry_run:
        say(f"\n✓  {out}", a.quiet)
    print(out)
    return 0


# ── Argument parsing ──────────────────────────────────────────────────────
def _add_common(p):
    p.add_argument("--dry-run", action="store_true",
                   help="only print the ffmpeg/rembg commands")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="no progress bar, no chatter (errors still print)")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    _add_common(common)

    p = argparse.ArgumentParser(
        prog="TransparentVideoMaker.py",
        description="Remove a solid-colour background (green screen) and write a "
                    "transparent video. Run without arguments for the GUI.",
        epilog="examples:\n"
               "  TransparentVideoMaker.py video -i clip.mp4\n"
               "  TransparentVideoMaker.py video -i clip.mp4 --method ai -m isnet-general-use\n"
               "  TransparentVideoMaker.py video -i clip.mp4 --color '#00b140' --similarity 0.12\n"
               "  TransparentVideoMaker.py video -i ./clips -o ./out -r --overwrite\n"
               "  TransparentVideoMaker.py frames -i ./frames --fps 30 -o out.webm\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    sub = p.add_subparsers(dest="cmd", metavar="{video,frames,gui}")

    v = sub.add_parser("video", parents=[common],
                       help="video or folder of videos → transparent video (3 steps)")
    v.add_argument("-i", "--input", required=True,
                   help="source video, or a folder to batch-convert every video in it")
    v.add_argument("-o", "--output",
                   help="output file, or output folder for batch mode")
    v.add_argument("-r", "--recursive", action="store_true",
                   help="with a folder input, also scan subfolders")
    v.add_argument("--method", choices=("chroma", "ai"), default="chroma",
                   help="chroma key (default) or rembg AI removal")
    v.add_argument("--color", default="#00ff00",
                   help="background colour for --method chroma, or 'auto' to "
                        "sample the top-left corner (default #00ff00)")
    v.add_argument("--similarity", type=float, default=0.10,
                   help="chroma similarity 0-1 (default 0.10)")
    v.add_argument("--blend", type=float, default=0.05,
                   help="chroma edge blend 0-1 (default 0.05)")
    v.add_argument("-m", "--model", default="u2net",
                   help="rembg model for --method ai (default u2net)")
    v.add_argument("--format", choices=("webm", "mov"), default="webm",
                   help="webm/VP9 (default) or mov/ProRes 4444")
    v.add_argument("--fps", type=float,
                   help="output frame rate (default: the source frame rate)")
    v.add_argument("--crf", type=int, default=25,
                   help="VP9 CRF 0-63, higher = smaller (default 25)")
    v.add_argument("--target-size", metavar="SIZE",
                   help="aim for this file size, e.g. 1.5M (webm only, "
                        "two-pass; re-encodes once if it overshoots)")
    v.add_argument("--jobs", type=int, default=1, metavar="N",
                   help="batch mode: convert N videos in parallel (default 1)")
    v.add_argument("--workdir", help="where the temp frame folders go")
    v.add_argument("--keep-frames", action="store_true",
                   help="keep the extracted / keyed frame folders")
    v.add_argument("--overwrite", action="store_true",
                   help="replace an existing output file")
    v.set_defaults(func=cmd_video)

    f = sub.add_parser("frames", parents=[common],
                       help="PNG sequence → transparent video")
    f.add_argument("-i", "--input", required=True, help="folder of PNG frames")
    f.add_argument("-o", "--output", help="output file")
    f.add_argument("--pattern", help="ffmpeg pattern, e.g. frame%%04d.png")
    f.add_argument("--fps", type=float, default=30.0,
                   help="input frame rate (default 30)")
    f.add_argument("--format", choices=("webm", "mov"), default="webm")
    f.add_argument("--crf", type=int, default=25)
    f.add_argument("--overwrite", action="store_true")
    f.set_defaults(func=cmd_frames)

    g = sub.add_parser("gui", help="open the desktop window")
    g.set_defaults(func=_cmd_gui)

    return p


def _cmd_gui(_args):
    from .app import App
    App().mainloop()
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
