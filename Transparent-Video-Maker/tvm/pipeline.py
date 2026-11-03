"""Transparent Video Maker — Full Pipeline tab: extract → matte → encode."""
import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

from .theme import *  # noqa: F401,F403
from .tools import REMBG_EXE, clean_env


class PipelineMixin:
    def _build_pipeline_pane(self):
        outer = tk.Frame(self.content, bg=BG)
        inner = scrollable(outer)
        P = {"padx": 24, "pady": 5}

        # 01 Input / Output
        make_section(inner, "01 — INPUT & OUTPUT")
        make_entry_row(inner, "Video file:",    self.video_path,  self._browse_video)
        make_entry_row(inner, "Output folder:", self.output_dir,  self._browse_output)

        # 02 Frame extraction
        make_section(inner, "02 — FRAME EXTRACTION  (ffmpeg)")
        fps_row = tk.Frame(inner, bg=BG)
        fps_row.pack(fill="x", **P)
        tk.Label(fps_row, text="Source FPS:", width=14, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=BG).pack(side="left")
        for v in ["24", "25", "30", "60"]:
            tk.Radiobutton(fps_row, text=v, variable=self.framerate, value=v,
                           font=("Consolas", 10), fg=TEXT, bg=BG,
                           selectcolor=PANEL, activebackground=BG,
                           activeforeground=ACCENT, relief="flat"
                           ).pack(side="left", padx=5)
        tk.Label(fps_row, text="custom →", font=("Consolas", 9),
                 fg=SUBTEXT, bg=BG).pack(side="left", padx=6)
        tk.Entry(fps_row, textvariable=self.framerate, width=5,
                 font=("Consolas", 10), bg=PANEL, fg=TEXT, insertbackground=TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT
                 ).pack(side="left", ipady=4)

        # 03 Background removal
        make_section(inner, "03 — BACKGROUND REMOVAL")

        mode_row = tk.Frame(inner, bg=BG)
        mode_row.pack(fill="x", **P)
        tk.Label(mode_row, text="Method:", width=14, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=BG).pack(side="left")
        for val, lbl in [("chroma", "Chroma Key  (solid color bg — RECOMMENDED)"),
                         ("ai",     "AI Model  (rembg)")]:
            tk.Radiobutton(mode_row, text=f"  {lbl}",
                           variable=self.remove_mode, value=val,
                           font=("Consolas", 10), fg=TEXT, bg=BG,
                           selectcolor=PANEL, activebackground=BG,
                           activeforeground=ACCENT, relief="flat",
                           command=self._toggle_mode
                           ).pack(side="left", padx=10)

        # Chroma / AI sub-panels share one grid cell, so switching the method
        # never re-orders the widgets below them.
        self.mode_box = tk.Frame(inner, bg=BG)
        self.mode_box.pack(fill="x", padx=32, pady=(2, 2))
        self.mode_box.columnconfigure(0, weight=1)

        # Chroma sub-panel
        self.chroma_panel = tk.Frame(self.mode_box, bg=PANEL2,
                                     highlightthickness=1,
                                     highlightbackground=BORDER)
        self.chroma_panel.grid(row=0, column=0, sticky="ew")

        # color row
        cr1 = tk.Frame(self.chroma_panel, bg=PANEL2)
        cr1.pack(fill="x", padx=12, pady=(8, 4))
        tk.Label(cr1, text="Background color:", width=18, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=PANEL2).pack(side="left")
        self.color_swatch = tk.Label(cr1, width=4, bg=self.chroma_color.get(),
                                     highlightthickness=1,
                                     highlightbackground=BORDER)
        self.color_swatch.pack(side="left", ipady=8, padx=(0, 6))
        tk.Entry(cr1, textvariable=self.chroma_color, width=10,
                 font=("Consolas", 10), bg=PANEL, fg=TEXT, insertbackground=TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT
                 ).pack(side="left", ipady=4, padx=(0, 6))
        make_btn(cr1, "Pick color", self._pick_color, small=True).pack(side="left", padx=4)
        tk.Label(cr1, text="  Presets:", font=("Consolas", 8),
                 fg=SUBTEXT, bg=PANEL2).pack(side="left")
        for name, col in [("Green",  "#00ff00"), ("Blue",  "#0000ff"),
                          ("Black",  "#000000"), ("White", "#ffffff")]:
            tk.Button(cr1, text=name, font=("Consolas", 8), fg=SUBTEXT,
                      bg=PANEL, relief="flat", cursor="hand2", padx=5, pady=2,
                      command=lambda c=col: self._set_color(c)
                      ).pack(side="left", padx=2)

        # similarity slider
        cr2 = tk.Frame(self.chroma_panel, bg=PANEL2)
        cr2.pack(fill="x", padx=12, pady=2)
        tk.Label(cr2, text="Similarity:", width=18, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=PANEL2).pack(side="left")
        tk.Scale(cr2, variable=self.chroma_sim, from_=0.01, to=0.50,
                 resolution=0.01, orient="horizontal", length=210,
                 bg=PANEL2, fg=TEXT, troughcolor=BG, highlightthickness=0,
                 activebackground=ACCENT, font=("Consolas", 8), sliderlength=14
                 ).pack(side="left", padx=(0, 8))
        tk.Label(cr2, text="How broadly to match the background color",
                 font=("Consolas", 8), fg=SUBTEXT, bg=PANEL2).pack(side="left")

        # blend slider
        cr3 = tk.Frame(self.chroma_panel, bg=PANEL2)
        cr3.pack(fill="x", padx=12, pady=(2, 8))
        tk.Label(cr3, text="Edge blend:", width=18, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=PANEL2).pack(side="left")
        tk.Scale(cr3, variable=self.chroma_blend, from_=0.0, to=0.30,
                 resolution=0.01, orient="horizontal", length=210,
                 bg=PANEL2, fg=TEXT, troughcolor=BG, highlightthickness=0,
                 activebackground=ACCENT, font=("Consolas", 8), sliderlength=14
                 ).pack(side="left", padx=(0, 8))
        tk.Label(cr3, text="Soften jagged edges (raise if outline looks harsh)",
                 font=("Consolas", 8), fg=SUBTEXT, bg=PANEL2).pack(side="left")

        # AI sub-panel
        self.ai_panel = tk.Frame(self.mode_box, bg=PANEL2,
                                  highlightthickness=1,
                                  highlightbackground=BORDER)
        self.ai_panel.grid(row=0, column=0, sticky="ew")
        self.ai_panel.grid_remove()
        mf = tk.Frame(self.ai_panel, bg=PANEL2)
        mf.pack(fill="x", padx=12, pady=8)
        for val, lbl in [
            ("u2net",            "u2net            — general purpose"),
            ("u2net_human_seg",  "u2net_human_seg  — people & characters"),
            ("isnet-general-use","isnet-general-use — sharper edges, slower"),
            ("silueta",          "silueta          — fast, simple backgrounds"),
        ]:
            tk.Radiobutton(mf, text=f"  {lbl}",
                           variable=self.rembg_model, value=val,
                           font=("Consolas", 9), fg=TEXT, bg=PANEL2,
                           anchor="w", selectcolor=PANEL,
                           activebackground=PANEL2, activeforeground=ACCENT,
                           relief="flat").pack(fill="x", pady=1)

        self._toggle_mode()

        # 04 live frame preview (throttled, one frame at a time)
        make_section(inner, "04 — FRAME PREVIEW  (one frame · live matte)")
        self._build_preview_section(inner)
        tk.Label(inner,
                 text="  Tune 03 above and drag the slider — the matte "
                      "re-renders as you go (throttled: latest frame wins).",
                 font=("Consolas", 8), fg=SUBTEXT, bg=BG, anchor="w"
                 ).pack(fill="x", padx=24, pady=(0, 6))

        # 05 Encode
        make_section(inner, "05 — VIDEO RE-ENCODE  (ffmpeg)")
        fmt_row = tk.Frame(inner, bg=BG)
        fmt_row.pack(fill="x", **P)
        tk.Label(fmt_row, text="Output format:", width=14, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=BG).pack(side="left")
        for val, lbl in [("webm", "WebM/VP9  — web, OBS, most editors"),
                         ("mov",  "MOV/ProRes 4444  — editing master")]:
            tk.Radiobutton(fmt_row, text=f"  {lbl}", variable=self.out_format,
                           value=val, font=("Consolas", 9), fg=TEXT, bg=BG,
                           selectcolor=PANEL, activebackground=BG,
                           activeforeground=ACCENT, relief="flat"
                           ).pack(side="left", padx=8)
        crf_row = tk.Frame(inner, bg=BG)
        crf_row.pack(fill="x", **P)
        tk.Label(crf_row, text="Quality (CRF):", width=14, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=BG).pack(side="left")
        tk.Scale(crf_row, variable=self.crf, from_=0, to=63,
                 orient="horizontal", length=230, bg=PANEL, fg=TEXT,
                 troughcolor=BG, highlightthickness=0, activebackground=ACCENT,
                 font=("Consolas", 8), sliderlength=14
                 ).pack(side="left", padx=(0, 8))
        tk.Label(crf_row, text="0 = best quality / largest file",
                 font=("Consolas", 8), fg=SUBTEXT, bg=BG).pack(side="left")
        kf_row = tk.Frame(inner, bg=BG)
        kf_row.pack(fill="x", **P)
        tk.Checkbutton(kf_row,
                       text="  Keep temporary frame folders after processing",
                       variable=self.keep_frames,
                       font=("Consolas", 9), fg=SUBTEXT, bg=BG,
                       selectcolor=PANEL, activebackground=BG,
                       activeforeground=TEXT, relief="flat").pack(side="left")

        # Controls
        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", padx=24, pady=10)
        ctrl = tk.Frame(inner, bg=BG)
        ctrl.pack(fill="x", padx=24, pady=4)
        self.run_btn = make_btn(ctrl, "▶  RUN FULL PIPELINE",
                                self._run_pipeline)
        self.run_btn.pack(side="left", ipadx=14, ipady=6)
        self.stop_btn = make_btn(ctrl, "■  STOP", self._stop, danger=True)
        self.stop_btn.pack(side="left", padx=8, ipadx=8, ipady=6)
        self.stop_btn.config(state="disabled")
        make_btn(ctrl, "Check tools", self._check_tools,
                 small=True).pack(side="right")

        # Step indicators
        sf = tk.Frame(inner, bg=BG)
        sf.pack(fill="x", padx=24, pady=(8, 4))
        self.pipe_steps = []
        for i, name in enumerate(["Extract Frames", "Remove BG", "Encode Video"]):
            f2 = tk.Frame(sf, bg=PANEL, padx=10, pady=8,
                          highlightthickness=1, highlightbackground=BORDER)
            f2.pack(side="left", fill="x", expand=True, padx=3)
            tk.Label(f2, text=f"STEP {i+1}", font=("Consolas", 8),
                     fg=SUBTEXT, bg=PANEL).pack(anchor="w")
            lbl = tk.Label(f2, text=name, font=("Consolas", 10, "bold"),
                           fg=SUBTEXT, bg=PANEL)
            lbl.pack(anchor="w")
            dot = tk.Label(f2, text="●  idle", font=("Consolas", 9),
                           fg=SUBTEXT, bg=PANEL)
            dot.pack(anchor="w")
            self.pipe_steps.append((f2, lbl, dot))

        pb = tk.Frame(inner, bg=BG)
        pb.pack(fill="x", padx=24, pady=(4, 0))
        s = ttk.Style(); s.theme_use("default")
        s.configure("A.Horizontal.TProgressbar",
                    troughcolor=PANEL, bordercolor=BORDER,
                    background=ACCENT, lightcolor=ACCENT2, darkcolor=ACCENT)
        self.pipe_pb = ttk.Progressbar(pb, style="A.Horizontal.TProgressbar",
                                       mode="indeterminate")
        self.pipe_pb.pack(fill="x")

        make_section(inner, "LOG", pady_top=8)
        self.pipe_log = make_log(inner)
        self._wlog(self.pipe_log,
                   "Ready. Select your video and click  ▶ RUN FULL PIPELINE.\n",
                   "head")
        return outer

    def _run_pipeline(self):
        video  = self.video_path.get().strip()
        outdir = self.output_dir.get().strip() or os.path.dirname(video)
        if not video or not os.path.isfile(video):
            messagebox.showerror("No video", "Please select a valid video file.")
            return
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.running = True
        self.pipe_pb.start(12)
        for i in range(3): self._set_step(i, "idle")
        threading.Thread(target=self._pipeline_thread,
                         args=(video, outdir), daemon=True).start()

    def _pipeline_thread(self, video, outdir):
        log = self.pipe_log
        try:
            base   = os.path.splitext(os.path.basename(video))[0]
            frames = os.path.join(outdir, f"{base}_frames")
            transp = os.path.join(outdir, f"{base}_transparent")
            fps    = self.framerate.get() or "30"
            os.makedirs(frames, exist_ok=True)

            # Step 1
            self._step_ui(0, "running")
            self._wlog(log, f"\n▶  STEP 1 — Extracting frames…\n", "head")
            ok = self._run_cmd(
                ["ffmpeg", "-y", "-i", video, "-pix_fmt", "rgba",
                 os.path.join(frames, "frame%04d.png")], log)
            if not ok:
                self._step_ui(0, "error"); return
            self._step_ui(0, "done")
            n = len([f for f in os.listdir(frames) if f.endswith(".png")])
            self._wlog(log, f"  → {n} frames extracted\n", "ok")

            # Step 2
            self._step_ui(1, "running")
            mode = self.remove_mode.get()
            if mode == "chroma":
                self._wlog(log, "\n▶  STEP 2 — Chroma key removal…\n", "head")
                os.makedirs(transp, exist_ok=True)
                hex_col  = self.chroma_color.get().lstrip("#")
                color_str = f"0x{hex_col.upper()}"
                sim      = round(self.chroma_sim.get(), 3)
                blend    = round(self.chroma_blend.get(), 3)
                self._wlog(log, f"  Color: #{hex_col.upper()}  "
                                 f"Similarity: {sim}  Blend: {blend}\n")
                vf  = f"chromakey={color_str}:{sim}:{blend}"
                ok  = self._run_cmd([
                    "ffmpeg", "-y",
                    "-i", os.path.join(frames, "frame%04d.png"),
                    "-vf", vf,
                    "-pix_fmt", "yuva420p",
                    os.path.join(transp, "frame%04d.png")
                ], log)
            else:
                os.makedirs(transp, exist_ok=True)
                self._wlog(log, f"\n▶  STEP 2 — AI removal "
                                 f"({self.rembg_model.get()})…\n", "head")
                cmd = [REMBG_EXE or "rembg", "p"]
                if self.rembg_model.get() != "u2net":
                    cmd += ["-m", self.rembg_model.get()]
                cmd += [frames, transp]
                ok = self._run_cmd(cmd, log)
            if not ok:
                self._step_ui(1, "error"); return
            self._step_ui(1, "done")

            # Step 3
            self._step_ui(2, "running")
            outfile = os.path.join(outdir,
                                   f"{base}_transparent.{self.out_format.get()}")
            self._wlog(log, f"\n▶  STEP 3 — Encoding {self.out_format.get()}…\n",
                       "head")
            ok = self._encode(transp, fps, self.crf.get(),
                              self.out_format.get(), outfile, log)
            if not ok:
                self._step_ui(2, "error"); return
            self._step_ui(2, "done")

            if not self.keep_frames.get():
                shutil.rmtree(frames, ignore_errors=True)
                shutil.rmtree(transp, ignore_errors=True)
                self._wlog(log, "  → Temp folders removed.\n")

            self._wlog(log, f"\n✓  ALL DONE!  →  {outfile}\n\n", "ok")
        except Exception as ex:
            self._wlog(log, f"\n  Unexpected error: {ex}\n", "err")
        finally:
            self.after(0, self._pipe_done)

    def _pipe_done(self):
        self.running = False
        self.pipe_pb.stop()
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
