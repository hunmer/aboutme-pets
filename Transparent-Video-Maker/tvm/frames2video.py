"""Transparent Video Maker — Frames → Video tab: stitch transparent PNGs into a video."""
import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

from .theme import *  # noqa: F401,F403


class FramesMixin:
    def _build_fv_pane(self):
        outer = tk.Frame(self.content, bg=BG)

        info = tk.Frame(outer, bg=PANEL2,
                        highlightthickness=1, highlightbackground=BORDER)
        info.pack(fill="x", padx=24, pady=(12, 0))
        tk.Label(info,
                 text="  Use this tab when you already have a folder of transparent PNG frames\n"
                      "  (e.g. manually edited in Photoshop or another tool) and just need\n"
                      "  them stitched into a transparent video.",
                 font=("Consolas", 9), fg=SUBTEXT, bg=PANEL2, justify="left"
                 ).pack(padx=10, pady=8, anchor="w")

        make_section(outer, "FRAMES FOLDER & OUTPUT")
        make_entry_row(outer, "Frames folder:", self.fv_folder,
                       self._fv_browse_folder)
        make_entry_row(outer, "Output folder:", self.fv_output,
                       self._fv_browse_output)

        make_section(outer, "FRAME FILENAME PATTERN")

        # Warning/status banner for pattern issues
        self.fv_pattern_banner = tk.Frame(outer, bg="#1a1500",
                                           highlightthickness=1,
                                           highlightbackground=WARNING)
        self.fv_pattern_banner_lbl = tk.Label(
            self.fv_pattern_banner,
            text="",
            font=("Consolas", 9), fg=WARNING, bg="#1a1500", justify="left")
        self.fv_pattern_banner_lbl.pack(padx=10, pady=6, anchor="w")
        # Hidden by default — shown when a bad pattern is detected

        pat_row = tk.Frame(outer, bg=BG)
        pat_row.pack(fill="x", padx=24, pady=5)
        tk.Label(pat_row, text="Pattern:", width=14, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=BG).pack(side="left")
        tk.Entry(pat_row, textvariable=self.fv_pattern, width=20,
                 font=("Consolas", 10), bg=PANEL, fg=TEXT, insertbackground=TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT
                 ).pack(side="left", ipady=5, padx=(0, 8))
        make_btn(pat_row, "Auto-detect", self._fv_autodetect,
                 small=True).pack(side="left", padx=(0, 6))
        tk.Label(pat_row, text="e.g.  frame%04d.png   img%03d.png",
                 font=("Consolas", 8), fg=SUBTEXT, bg=BG).pack(side="left", padx=4)

        # Rename frames section
        rename_frame = tk.Frame(outer, bg=PANEL2,
                                highlightthickness=1, highlightbackground=BORDER)
        rename_frame.pack(fill="x", padx=24, pady=(2, 4))
        rn_left = tk.Frame(rename_frame, bg=PANEL2)
        rn_left.pack(side="left", fill="x", expand=True, padx=12, pady=8)
        tk.Label(rn_left,
                 text="If your files have an unsupported name format  (e.g.  01 (1).png,  01 (2).png ...)",
                 font=("Consolas", 9), fg=TEXT, bg=PANEL2).pack(anchor="w")
        tk.Label(rn_left,
                 text="use Rename Frames to rename them to  frame0001.png, frame0002.png ...  in sorted order.",
                 font=("Consolas", 9), fg=SUBTEXT, bg=PANEL2).pack(anchor="w")
        make_btn(rename_frame, "⟳  Rename Frames", self._fv_rename_frames,
                 accent=True).pack(side="right", padx=12, pady=8, ipadx=8, ipady=4)

        make_section(outer, "OUTPUT SETTINGS")
        fps_row = tk.Frame(outer, bg=BG)
        fps_row.pack(fill="x", padx=24, pady=5)
        tk.Label(fps_row, text="Framerate:", width=14, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=BG).pack(side="left")
        for v in ["24", "25", "30", "60"]:
            tk.Radiobutton(fps_row, text=v, variable=self.fv_fps, value=v,
                           font=("Consolas", 10), fg=TEXT, bg=BG,
                           selectcolor=PANEL, activebackground=BG,
                           activeforeground=ACCENT, relief="flat"
                           ).pack(side="left", padx=5)
        tk.Label(fps_row, text="custom →", font=("Consolas", 9),
                 fg=SUBTEXT, bg=BG).pack(side="left", padx=6)
        tk.Entry(fps_row, textvariable=self.fv_fps, width=5,
                 font=("Consolas", 10), bg=PANEL, fg=TEXT, insertbackground=TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT
                 ).pack(side="left", ipady=4)

        fmt_row = tk.Frame(outer, bg=BG)
        fmt_row.pack(fill="x", padx=24, pady=5)
        tk.Label(fmt_row, text="Output format:", width=14, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=BG).pack(side="left")
        for val, lbl in [("webm", "WebM/VP9  — web, OBS, most editors"),
                         ("mov",  "MOV/ProRes 4444  — editing master")]:
            tk.Radiobutton(fmt_row, text=f"  {lbl}",
                           variable=self.fv_format, value=val,
                           font=("Consolas", 9), fg=TEXT, bg=BG,
                           selectcolor=PANEL, activebackground=BG,
                           activeforeground=ACCENT, relief="flat"
                           ).pack(side="left", padx=8)

        crf_row = tk.Frame(outer, bg=BG)
        crf_row.pack(fill="x", padx=24, pady=5)
        tk.Label(crf_row, text="Quality (CRF):", width=14, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=BG).pack(side="left")
        tk.Scale(crf_row, variable=self.fv_crf, from_=0, to=63,
                 orient="horizontal", length=230, bg=PANEL, fg=TEXT,
                 troughcolor=BG, highlightthickness=0, activebackground=ACCENT,
                 font=("Consolas", 8), sliderlength=14
                 ).pack(side="left", padx=(0, 8))
        tk.Label(crf_row, text="0 = best quality / largest file",
                 font=("Consolas", 8), fg=SUBTEXT, bg=BG).pack(side="left")

        tk.Frame(outer, bg=BORDER, height=1).pack(fill="x", padx=24, pady=10)
        ctrl = tk.Frame(outer, bg=BG)
        ctrl.pack(fill="x", padx=24, pady=4)
        self.fv_run_btn = make_btn(ctrl, "▶  ENCODE VIDEO FROM FRAMES",
                                   self._run_fv, accent=True)
        self.fv_run_btn.pack(side="left", ipadx=14, ipady=6)
        self.fv_stop_btn = make_btn(ctrl, "■  STOP", self._stop, danger=True)
        self.fv_stop_btn.pack(side="left", padx=8, ipadx=8, ipady=6)
        self.fv_stop_btn.config(state="disabled")

        pb2 = tk.Frame(outer, bg=BG)
        pb2.pack(fill="x", padx=24, pady=(8, 0))
        self.fv_pb = ttk.Progressbar(pb2, style="A.Horizontal.TProgressbar",
                                     mode="indeterminate")
        self.fv_pb.pack(fill="x")

        make_section(outer, "LOG", pady_top=8)
        self.fv_log = make_log(outer)
        self._wlog(self.fv_log,
                   "Select a folder of transparent PNG frames, then click "
                   "▶ ENCODE VIDEO FROM FRAMES.\n", "head")
        return outer

    def _fv_browse_folder(self):
        p = filedialog.askdirectory(title="Select frames folder")
        if p:
            self.fv_folder.set(p)
            if not self.fv_output.get():
                self.fv_output.set(os.path.dirname(p))
            self._fv_autodetect()

    def _fv_browse_output(self):
        p = filedialog.askdirectory(title="Select output folder")
        if p: self.fv_output.set(p)

    def _fv_autodetect(self):
        folder = self.fv_folder.get()
        if not folder or not os.path.isdir(folder):
            return
        pngs = sorted([f for f in os.listdir(folder)
                       if f.lower().endswith(".png")])
        if not pngs:
            self._wlog(self.fv_log, "  No PNG files found in that folder.\n", "warn")
            return
        sample = pngs[0]
        m_std = re.match(r'^([a-zA-Z_\-]+)(\d+)\.png$', sample)
        m_copy = re.match(r'^(.+?)\s*\((\d+)\)\.png$', sample)
        if m_std:
            prefix = m_std.group(1)
            width  = len(m_std.group(2))
            pat    = f"{prefix}%0{width}d.png"
            self.fv_pattern.set(pat)
            self._fv_hide_banner()
            self._wlog(self.fv_log,
                       f"  Auto-detected pattern: {pat}  ({len(pngs)} frames)\n",
                       "ok")
        elif m_copy:
            self.fv_pattern.set("— rename required —")
            self._fv_show_banner(
                f"  ⚠  Unsupported filename format:  {sample}\n"
                f"  ffmpeg cannot read files named like  01 (1).png, 01 (2).png ...\n"
                f"  Click  ⟳ Rename Frames  to rename them to  frame0001.png, frame0002.png ...\n"
                f"  ({len(pngs)} frames found)"
            )
            self._wlog(self.fv_log,
                       f"  ⚠  Copy-style filenames detected ({sample}).\n"
                       f"  Use  ⟳ Rename Frames  to fix this automatically.\n",
                       "warn")
        else:
            self._wlog(self.fv_log,
                       f"  Could not auto-detect pattern.\n"
                       f"  First file: {sample}\n"
                       f"  Try  ⟳ Rename Frames  to normalize the filenames.\n",
                       "warn")

    def _fv_show_banner(self, msg):
        self.fv_pattern_banner_lbl.config(text=msg)
        self.fv_pattern_banner.pack(fill="x", padx=24, pady=(0, 4))

    def _fv_hide_banner(self):
        self.fv_pattern_banner.pack_forget()

    def _fv_rename_frames(self):
        folder = self.fv_folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("No folder", "Please select a frames folder first.")
            return
        pngs = sorted([f for f in os.listdir(folder) if f.lower().endswith(".png")])
        if not pngs:
            messagebox.showerror("No frames", "No PNG files found in that folder.")
            return
        # Check if already in standard format
        if re.match(r'^frame\d{4}\.png$', pngs[0]):
            messagebox.showinfo("Already clean",
                                "Files are already named frame0001.png etc. — no rename needed.")
            return
        # Confirm
        ans = messagebox.askyesno(
            "Rename frames?",
            f"This will rename {len(pngs)} PNG files in:\n{folder}\n\n"
            f"  {pngs[0]}  →  frame0001.png\n"
            f"  {pngs[1] if len(pngs) > 1 else '...'}  →  frame0002.png\n"
            f"  ...\n\n"
            f"The original names will be lost. Continue?"
        )
        if not ans:
            return
        # Rename via temp names first to avoid collisions
        self._wlog(self.fv_log,
                   f"\n⟳  Renaming {len(pngs)} frames...\n", "head")
        try:
            tmp_names = []
            for i, name in enumerate(pngs):
                src = os.path.join(folder, name)
                tmp = os.path.join(folder, f"__tmp_rename_{i:06d}__.png")
                os.rename(src, tmp)
                tmp_names.append(tmp)
            for i, tmp in enumerate(tmp_names):
                dst = os.path.join(folder, f"frame{i+1:04d}.png")
                os.rename(tmp, dst)
            self._wlog(self.fv_log,
                       f"  ✓  Renamed {len(pngs)} files to frame0001.png ... "
                       f"frame{len(pngs):04d}.png\n", "ok")
            self.fv_pattern.set("frame%04d.png")
            self._fv_hide_banner()
            self._wlog(self.fv_log,
                       f"  Pattern set to  frame%04d.png  — ready to encode.\n",
                       "ok")
        except Exception as ex:
            self._wlog(self.fv_log, f"  Error during rename: {ex}\n", "err")

    def _run_fv(self):
        folder = self.fv_folder.get().strip()
        outdir = self.fv_output.get().strip() or os.path.dirname(folder)
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("No folder", "Please select a folder of PNG frames.")
            return
        self._save_settings()   # crash-safe: config used for this run is kept
        self.fv_run_btn.config(state="disabled")
        self.fv_stop_btn.config(state="normal")
        self.running = True
        self.fv_pb.start(12)
        threading.Thread(target=self._fv_thread,
                         args=(folder, outdir), daemon=True).start()

    def _fv_thread(self, folder, outdir):
        log = self.fv_log
        try:
            fps     = self.fv_fps.get() or "30"
            crf     = self.fv_crf.get()
            fmt     = self.fv_format.get()
            pattern = self.fv_pattern.get().strip() or "frame%04d.png"
            name    = os.path.basename(folder.rstrip("/\\"))
            outfile = os.path.join(outdir, f"{name}_video.{fmt}")
            self._wlog(log,
                       f"\n▶  Encoding from: {folder}\n"
                       f"   Pattern: {pattern}   FPS: {fps}   "
                       f"CRF: {crf}   Format: {fmt}\n", "head")
            ok = self._encode(folder, fps, crf, fmt, outfile, log,
                              pattern=pattern)
            if ok:
                self._wlog(log, f"\n✓  DONE!  →  {outfile}\n\n", "ok")
            else:
                self._wlog(log, "\n  Encoding failed — check log above.\n", "err")
        except Exception as ex:
            self._wlog(log, f"\n  Error: {ex}\n", "err")
        finally:
            self.after(0, self._fv_done)

    def _fv_done(self):
        self.running = False
        self.fv_pb.stop()
        self.fv_run_btn.config(state="normal")
        self.fv_stop_btn.config(state="disabled")
