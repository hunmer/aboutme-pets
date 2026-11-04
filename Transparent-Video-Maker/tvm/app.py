"""Application shell: window, shared state, and helpers used by both tabs."""
import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

from .theme import *  # noqa: F401,F403
from .tools import REMBG_EXE, clean_env, find_videos, output_path, plan_batch
from .encode import encode_cmd
from .settings import load_settings, save_settings
from .preview import PreviewMixin
from .pipeline import PipelineMixin
from .frames2video import FramesMixin


class App(PreviewMixin, PipelineMixin, FramesMixin, tk.Tk):
    # Form fields persisted to tvm_settings.json and restored on launch.
    SETTINGS_KEYS = (
        # Pipeline tab
        "video_path", "output_dir", "batch_dir",
        "batch_recursive", "batch_skip_existing", "batch_on_error",
        "batch_workers",
        "framerate", "crf", "out_format", "remove_mode",
        "chroma_color", "chroma_sim", "chroma_blend",
        "rembg_model", "keep_frames",
        # Frames → Video tab
        "fv_folder", "fv_output", "fv_fps", "fv_crf", "fv_format",
        "fv_pattern",
    )

    def __init__(self):
        super().__init__()
        self.title("Transparent Video Maker  v2")
        self.configure(bg=BG)
        self.minsize(740, 780)
        self.geometry("800x920")

        self.running = False
        self.proc = None
        # Parallel batch: every live process is registered here so STOP can
        # kill them all (the single self.proc slot can't hold N workers).
        self._procs = {}
        self._procs_lock = threading.Lock()

        # ── Pipeline vars ──────────────────────────────────────────────────
        self.video_path   = tk.StringVar()
        self.output_dir   = tk.StringVar()
        self.batch_dir    = tk.StringVar()
        self.batch_recursive    = tk.BooleanVar(value=False)
        self.batch_skip_existing = tk.BooleanVar(value=False)
        self.batch_on_error     = tk.StringVar(value="continue")
        self.batch_workers      = tk.IntVar(value=2)
        self.framerate    = tk.StringVar(value="30")
        self.crf          = tk.IntVar(value=25)
        self.out_format   = tk.StringVar(value="webm")
        self.remove_mode  = tk.StringVar(value="chroma")
        self.chroma_color = tk.StringVar(value="#00ff00")
        self.chroma_sim   = tk.DoubleVar(value=0.10)
        self.chroma_blend = tk.DoubleVar(value=0.05)
        self.rembg_model  = tk.StringVar(value="u2net")
        self.keep_frames  = tk.BooleanVar(value=False)

        # ── Frames→Video vars ──────────────────────────────────────────────
        self.fv_folder  = tk.StringVar()
        self.fv_output  = tk.StringVar()
        self.fv_fps     = tk.StringVar(value="30")
        self.fv_crf     = tk.IntVar(value=25)
        self.fv_format  = tk.StringVar(value="webm")
        self.fv_pattern = tk.StringVar(value="frame%04d.png")

        # ── Frame-preview state ────────────────────────────────────────────
        self.prev_frame   = tk.DoubleVar(value=0)
        self.prev_fps     = 30.0
        self.prev_frames  = 0
        self.prev_dur     = 0.0
        self.prev_src_w   = 0
        self.prev_src_h   = 0
        self._pv_seq      = 0
        self._pv_busy     = False
        self._pv_pending  = False
        self._pv_timer    = None
        self._pv_last     = 0.0
        self._prev_rendered    = False
        self._prev_probe_seq   = 0
        self._prev_probe_after = None
        self._prev_q      = queue.Queue()
        self._swatches    = []
        self._prev_color_auto    = True
        self._prev_color_setting = False

        # keep the mode panels in sync when the mode changes from code, too
        self.remove_mode.trace_add("write", lambda *a: self._toggle_mode())

        self._build_ui()
        self._restore_settings()   # recall the last session's form values
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Header + tab bar ──────────────────────────────────────────────────
    def _build_ui(self):
        hdr = tk.Frame(self, bg=BG, pady=14)
        hdr.pack(fill="x", padx=24)
        tk.Label(hdr, text="◈  TRANSPARENT VIDEO MAKER",
                 font=("Consolas", 16, "bold"), fg=ACCENT, bg=BG).pack(side="left")
        tk.Label(hdr, text="ffmpeg + rembg",
                 font=("Consolas", 9), fg=SUBTEXT, bg=BG).pack(side="left", padx=12)
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24)

        tab_bar = tk.Frame(self, bg=BG)
        tab_bar.pack(fill="x", padx=24, pady=(8, 0))
        self.tab_btns = []
        for i, lbl in enumerate(["  Full Pipeline  ", "  Frames → Video  "]):
            b = tk.Button(tab_bar, text=lbl,
                          font=("Consolas", 10, "bold"),
                          fg=SUBTEXT, bg=PANEL, relief="flat",
                          bd=0, padx=10, pady=6, cursor="hand2",
                          command=lambda i=i: self._switch_tab(i))
            b.pack(side="left", padx=(0, 4))
            self.tab_btns.append(b)

        self.content = tk.Frame(self, bg=BG)
        self.content.pack(fill="both", expand=True)

        self.pane_pipe = self._build_pipeline_pane()
        self.pane_fv   = self._build_fv_pane()
        self._switch_tab(0)

        # Live preview reacts to anything that changes the matte.
        self.prev_frame.trace_add("write", lambda *a: self._prev_frame_moved())
        self.video_path.trace_add("write", lambda *a: self._prev_video_changed())
        self.chroma_color.trace_add("write", lambda *a: self._prev_color_touched())
        for var in (self.chroma_sim, self.chroma_blend,
                    self.remove_mode, self.rembg_model):
            var.trace_add("write", lambda *a: self._prev_settings_changed())

        # Batch info line follows the batch settings (recursive / skip),
        # the chosen folder, and the output dir (skip-existing counts).
        for var in (self.batch_recursive, self.batch_skip_existing,
                    self.batch_dir, self.output_dir):
            var.trace_add("write", lambda *a: self._update_batch_info())

        # Worker threads never touch Tk: they push results here and the main
        # thread drains the queue on its own event-loop tick.
        self.after(80, self._prev_poll)

    def _switch_tab(self, idx):
        panes = [self.pane_pipe, self.pane_fv]
        for p in panes:
            p.pack_forget()
        panes[idx].pack(fill="both", expand=True)
        for i, b in enumerate(self.tab_btns):
            if i == idx:
                b.config(fg=ACCENT, bg=PANEL2,
                         highlightthickness=1, highlightbackground=ACCENT)
            else:
                b.config(fg=SUBTEXT, bg=PANEL, highlightthickness=0)
        if idx == 0:
            self._prev_on_show()

    # ── Mode panels ───────────────────────────────────────────────────────
    def _toggle_mode(self):
        chroma = self.remove_mode.get() == "chroma"
        for panel, is_chroma in ((getattr(self, "chroma_panel", None), True),
                                 (getattr(self, "ai_panel", None), False)):
            if panel is None:
                continue
            if chroma == is_chroma:
                panel.grid()
            else:
                panel.grid_remove()

    # ── Colour helpers ────────────────────────────────────────────────────
    def _pick_color(self):
        col = colorchooser.askcolor(color=self.chroma_color.get(),
                                    title="Pick background color")[1]
        if col:
            self._set_color(col)

    def _set_color(self, col):
        self.chroma_color.set(col)
        self.color_swatch.config(bg=col)
        for sw in self._swatches:
            try:
                sw.config(bg=col)
            except Exception:
                pass

    # ── Browse callbacks ──────────────────────────────────────────────────
    def _browse_video(self):
        p = filedialog.askopenfilename(
            title="Select video",
            filetypes=[("Video files", "*.mp4 *.mov *.avi *.webm *.mkv"),
                       ("All files", "*.*")])
        if p:
            self.video_path.set(p)
            if not self.output_dir.get():
                self.output_dir.set(os.path.dirname(p))

    def _browse_output(self):
        p = filedialog.askdirectory(title="Select output folder")
        if p:
            self.output_dir.set(p)

    # ── Batch (folder) import ────────────────────────────────────────────
    def _browse_batch(self):
        p = filedialog.askdirectory(title="Select folder of videos")
        if not p:
            return
        self.batch_dir.set(p)
        videos = find_videos(p, recursive=self.batch_recursive.get())
        if videos:
            # Preview keeps tuning on the first clip of the batch.
            self.video_path.set(videos[0])
            if not self.output_dir.get():
                self.output_dir.set(p)
        self._update_batch_info()
        self._wlog(self.pipe_log,
                   f"\n─── Folder imported: {p}\n"
                   f"     {len(videos)} video(s) found — RUN now processes "
                   f"them all.\n", "head" if videos else "warn")

    def _clear_batch(self):
        if self.batch_dir.get():
            self.batch_dir.set("")
            self._update_batch_info()
            self._wlog(self.pipe_log,
                       "\n     Folder import cleared — back to single-video "
                       "mode.\n")

    def _update_batch_info(self):
        lbl = getattr(self, "batch_info_lbl", None)
        if lbl is None:
            return
        folder = self.batch_dir.get().strip()
        if not folder:
            lbl.config(text="", fg=SUBTEXT)
            return
        videos = find_videos(folder, recursive=self.batch_recursive.get())
        if not videos:
            lbl.config(text="●  no videos found in this folder "
                            "(mp4 / mov / avi / webm / mkv)", fg=WARNING)
            return
        scope = "incl. subfolders" if self.batch_recursive.get() \
            else "top level only"
        if self.batch_skip_existing.get():
            outdir = self.output_dir.get().strip() or folder
            plan = plan_batch(videos, outdir, self.out_format.get(),
                              skip_existing=True)
            done = len(videos) - len(plan)
            lbl.config(text=f"●  {len(plan)} to process · {done} already done "
                            f"· {len(videos)} found ({scope})",
                       fg=SUCCESS if plan else WARNING)
        else:
            lbl.config(text=f"●  {len(videos)} videos queued ({scope}) — RUN "
                            "processes every clip", fg=SUCCESS)

    # ── Tool check ────────────────────────────────────────────────────────
    def _check_tools(self):
        self._wlog(self.pipe_log,
                   "\n─── Tool Check ─────────────────────────────────\n", "head")
        for tool, cmd in [("ffmpeg", ["ffmpeg", "-version"]),
                          ("rembg",  [REMBG_EXE or "rembg", "--help"])]:
            try:
                subprocess.run(cmd, capture_output=True, timeout=8,
                               env=clean_env())
                extra = f"  ({cmd[0]})" if tool == "rembg" else ""
                self._wlog(self.pipe_log, f"  ✓  {tool} found{extra}\n", "ok")
            except FileNotFoundError:
                self._wlog(self.pipe_log, f"  ✗  {tool} NOT found\n", "err")
        if REMBG_EXE:
            self._wlog(self.pipe_log, f"  rembg: {REMBG_EXE}\n", "ok")
        self._wlog(self.pipe_log,
                   "\n  Missing something?\n"
                   "  • ffmpeg → https://www.gyan.dev/ffmpeg/builds/\n"
                   "  • rembg  → pip install rembg[cli]\n\n", "warn")

    # ── Log helper ────────────────────────────────────────────────────────
    def _wlog(self, widget, msg, tag=None):
        widget.configure(state="normal")
        widget.insert("end", msg, tag) if tag else widget.insert("end", msg)
        widget.see("end")
        widget.configure(state="disabled")
        self.update_idletasks()

    # ── Step indicator ────────────────────────────────────────────────────
    def _set_step(self, idx, state):
        STATES = {
            "idle":    (SUBTEXT, SUBTEXT, "●  idle"),
            "running": (TEXT,    ACCENT2, "◌  running…"),
            "done":    (TEXT,    SUCCESS, "✓  done"),
            "error":   (TEXT,    ERROR,   "✗  error"),
        }
        f, lbl, dot = self.pipe_steps[idx]
        nc, dc, dt = STATES[state]
        lbl.config(fg=nc)
        dot.config(fg=dc, text=dt)
        hl = {"running": ACCENT, "done": SUCCESS,
              "error": ERROR}.get(state, BORDER)
        f.config(highlightbackground=hl)

    def _step_ui(self, idx, state):
        self.after(0, lambda: self._set_step(idx, state))

    # ── Run command ───────────────────────────────────────────────────────
    def _run_cmd(self, cmd, log, tag=""):
        self._wlog(log, f"{tag}  $ {' '.join(cmd)}\n")
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env=clean_env())
            with self._procs_lock:
                self._procs[id(proc)] = proc
            self.proc = proc
            for line in proc.stdout:
                s = line.rstrip()
                if s:
                    self._wlog(log, f"{tag}    {s}\n")
            proc.wait()
            return proc.returncode == 0
        except FileNotFoundError as e:
            self._wlog(log, f"{tag}  Command not found: {e}\n", "err")
            return False
        finally:
            if proc is not None:
                with self._procs_lock:
                    self._procs.pop(id(proc), None)

    # ── Encode helper (shared) ────────────────────────────────────────────
    def _encode(self, frames_dir, fps, crf, fmt, outfile, log,
                pattern="frame%04d.png", tag=""):
        cmd = encode_cmd(frames_dir, fps, crf, fmt, outfile, pattern=pattern)
        return self._run_cmd(cmd, log, tag)

    # ── Stop ──────────────────────────────────────────────────────────────
    def _stop(self):
        self.running = False
        with self._procs_lock:
            procs = list(self._procs.values())
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        self._pipe_done()
        self._fv_done()
        self._wlog(self.pipe_log, "\n  ■  Stopped by user.\n", "warn")

    # ── Settings persistence ──────────────────────────────────────────────
    def _collect_settings(self):
        """Snapshot every persisted form field into a plain dict."""
        out = {}
        for key in self.SETTINGS_KEYS:
            try:
                out[key] = getattr(self, key).get()
            except Exception:
                pass
        return out

    def _save_settings(self):
        """Persist the current form; called on close and on every run."""
        save_settings(self._collect_settings())

    def _restore_settings(self):
        """Re-apply the last session's values. Runs after _build_ui so all
        traced callbacks (mode panels, batch info, preview) react normally.
        Bad/missing entries are skipped, never fatal."""
        data = load_settings()
        for key in self.SETTINGS_KEYS:
            val = data.get(key)
            if val is None or val == "":
                continue
            try:
                getattr(self, key).set(val)
            except Exception:
                pass

    # ── Shutdown ──────────────────────────────────────────────────────────
    def _on_close(self):
        self._save_settings()
        for attr in ("_pv_timer", "_prev_probe_after"):
            t = getattr(self, attr, None)
            if t is not None:
                try:
                    self.after_cancel(t)
                except Exception:
                    pass
        self.destroy()
