"""Live matte preview: one frame of the video, rendered off the UI thread.

The preview is embedded in the Full Pipeline tab and throttled: a burst of
slider drags or setting changes is coalesced, at most one render is in
flight, and the last request always wins.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk

from .theme import *  # noqa: F401,F403
from .tools import REMBG_EXE, clean_env


class PreviewMixin:
    PREV_W, PREV_H = 352, 264
    CHECK_BG = "0x1b2029"
    CHECK_FG = "0x262c38"
    GAP_CHROMA = 0.10          # seconds between chroma renders
    GAP_AI = 0.25              # AI renders are slow — fewer, newer frames

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_preview_section(self, parent):
        frow = tk.Frame(parent, bg=BG)
        frow.pack(fill="x", padx=24, pady=(4, 6))
        tk.Label(frow, text="Frame:", width=14, anchor="w",
                 font=("Consolas", 10), fg=SUBTEXT, bg=BG).pack(side="left")
        make_btn(frow, "◀", lambda: self._prev_step(-1),
                 small=True).pack(side="left", padx=(0, 4))
        self.prev_scale = tk.Scale(frow, variable=self.prev_frame,
                                   from_=0, to=100,
                                   orient="horizontal", length=300,
                                   bg=PANEL, fg=TEXT, troughcolor=BG,
                                   highlightthickness=0, activebackground=ACCENT,
                                   font=("Consolas", 8), sliderlength=14,
                                   showvalue=False)
        self.prev_scale.pack(side="left", padx=(0, 4))
        make_btn(frow, "▶", lambda: self._prev_step(1),
                 small=True).pack(side="left", padx=(0, 10))
        self.prev_time_lbl = tk.Label(frow, text="", width=22, anchor="w",
                                      font=("Consolas", 9), fg=ACCENT2, bg=BG)
        self.prev_time_lbl.pack(side="left")
        make_btn(frow, "↻  Re-render", lambda: self._prev_request(force=True),
                 small=True).pack(side="right")

        pics = tk.Frame(parent, bg=BG)
        pics.pack(fill="x", padx=20, pady=(2, 0))
        pics.columnconfigure(0, weight=1, uniform="p")
        pics.columnconfigure(1, weight=1, uniform="p")
        self.prev_cv_orig = self._prev_canvas(pics, 0, "ORIGINAL")
        self.prev_cv_res = self._prev_canvas(
            pics, 1, "RESULT  (alpha over checkerboard)")

        self.prev_status = tk.Label(parent, text="Select a video to start.",
                                    font=("Consolas", 9), fg=SUBTEXT, bg=BG,
                                    anchor="w")
        self.prev_status.pack(fill="x", padx=24, pady=(8, 4))

    def _prev_canvas(self, parent, col, title):
        tk.Label(parent, text=title, font=("Consolas", 8, "bold"),
                 fg=SUBTEXT, bg=BG).grid(row=0, column=col, sticky="w",
                                         padx=6, pady=(2, 0))
        cv = tk.Canvas(parent, width=self.PREV_W + 8, height=self.PREV_H + 8,
                       bg=PANEL, highlightthickness=1,
                       highlightbackground=BORDER)
        cv.grid(row=1, column=col, sticky="n", padx=4, pady=(2, 4))
        cv.create_text((self.PREV_W + 8) // 2, (self.PREV_H + 8) // 2,
                       text="no frame yet", font=("Consolas", 9),
                       fill=SUBTEXT, tags="ph")
        return cv

    # ── scheduling: debounce-free throttling ──────────────────────────────
    def _prev_visible(self):
        try:
            return bool(self.pane_pipe.winfo_ismapped())
        except Exception:
            return False

    def _prev_request(self, force=False):
        """Ask for a render — cheap, safe to call on every slider tick."""
        self._pv_pending = True
        if force:
            self._pv_last = 0.0
            if self._pv_timer is not None:
                try:
                    self.after_cancel(self._pv_timer)
                except Exception:
                    pass
                self._pv_timer = None
        self._pv_kick()

    def _pv_kick(self):
        if not self._pv_pending or self._pv_busy or self._pv_timer is not None:
            return
        gap = self.GAP_AI if self.remove_mode.get() == "ai" else self.GAP_CHROMA
        wait = max(0.0, gap - (time.monotonic() - self._pv_last))
        self._pv_timer = self.after(int(wait * 1000) + 1, self._pv_start)

    def _pv_start(self):
        self._pv_timer = None
        if not self._pv_pending or self._pv_busy:
            return
        if not self._prev_visible():
            return                       # keep pending; kicked when shown
        video = self.video_path.get().strip()
        if not video or not os.path.isfile(video):
            self._pv_pending = False
            self.prev_status.config(text="Select a video to start.", fg=SUBTEXT)
            return
        if not self.prev_frames:
            self._pv_pending = False
            self._prev_probe()
            return

        self._pv_pending = False
        self._pv_busy = True
        self._pv_last = time.monotonic()

        idx = int(self.prev_frame.get())
        w, h = self._prev_box()
        cfg = {
            "mode":  self.remove_mode.get(),
            "color": self.chroma_color.get(),
            "sim":   self.chroma_sim.get(),
            "blend": self.chroma_blend.get(),
            "model": self.rembg_model.get(),
            "fps":   self.prev_fps or 30.0,
            "w":     w,
            "h":     h,
        }
        self._pv_seq += 1
        seq = self._pv_seq
        self.prev_status.config(text="rendering…", fg=ACCENT2)
        threading.Thread(target=self._prev_worker,
                         args=(video, idx, cfg, seq), daemon=True).start()

    def _pv_release(self):
        self._pv_busy = False
        if self._pv_pending:
            self.after(30, self._pv_kick)

    # ── change hooks ──────────────────────────────────────────────────────
    def _prev_on_show(self):
        if self.video_path.get().strip():
            if not self.prev_frames:
                self._prev_probe()
            else:
                self._prev_request()
        self._pv_kick()

    def _prev_video_changed(self):
        self._prev_rendered = False
        self.prev_frames = 0
        self._prev_color_auto = True
        if self._prev_probe_after is not None:
            try:
                self.after_cancel(self._prev_probe_after)
            except Exception:
                pass
        self._prev_probe_after = self.after(300, self._prev_probe)

    def _prev_color_touched(self):
        if not self._prev_color_setting:
            self._prev_color_auto = False
        self._prev_settings_changed()

    def _prev_settings_changed(self):
        for sw in self._swatches:
            try:
                sw.config(bg=self.chroma_color.get())
            except Exception:
                pass
        if self.prev_frames:
            self._prev_request()

    def _prev_frame_moved(self):
        self._prev_update_time()
        if self.prev_frames:
            self._prev_request()

    def _prev_step(self, delta):
        if not self.prev_frames:
            return
        idx = int(self.prev_frame.get()) + delta
        self.prev_frame.set(max(0, min(self.prev_frames - 1, idx)))

    def _prev_update_time(self):
        if not self.prev_frames:
            self.prev_time_lbl.config(text="")
            return
        idx = int(self.prev_frame.get())
        t = idx / (self.prev_fps or 30.0)
        self.prev_time_lbl.config(
            text=f"{idx + 1} / {self.prev_frames}   ·   {t:.2f} s")

    def _prev_box(self):
        """Render size: the video's aspect fitted into the fixed canvas."""
        w, h = self.PREV_W, self.PREV_H
        if self.prev_src_w > 0 and self.prev_src_h > 0:
            ar = self.prev_src_w / float(self.prev_src_h)
            if w / float(h) > ar:
                w = h * ar
            else:
                h = w / ar
        return (max(16, int(w) // 2 * 2), max(16, int(h) // 2 * 2))

    # ── probing the video ─────────────────────────────────────────────────
    def _prev_probe(self):
        self._prev_probe_after = None
        video = self.video_path.get().strip()
        self._prev_probe_seq += 1
        seq = self._prev_probe_seq
        if not video or not os.path.isfile(video):
            self.prev_frames = 0
            self.prev_time_lbl.config(text="")
            self.prev_status.config(text="Select a video to start.", fg=SUBTEXT)
            return
        self.prev_status.config(text="Reading video info…", fg=ACCENT2)
        threading.Thread(target=self._prev_probe_worker,
                         args=(video, seq), daemon=True).start()

    def _prev_probe_worker(self, video, seq):
        info = {"fps": 30.0, "frames": 0, "dur": 0.0, "err": None}
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries",
                 "stream=avg_frame_rate,r_frame_rate,nb_frames,duration,width,height",
                 "-show_entries", "format=duration", "-of", "json", video],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30, env=clean_env()).stdout
            data = json.loads(out or "{}")
            st = (data.get("streams") or [{}])[0]
            fr = st.get("avg_frame_rate") or st.get("r_frame_rate") or "30/1"
            num, _, den = fr.partition("/")
            fps = float(num) / float(den or 1) if num else 30.0
            dur = float(st.get("duration")
                        or (data.get("format") or {}).get("duration") or 0.0)
            n = int(st.get("nb_frames") or 0) or int(round(dur * fps))
            info.update(fps=fps if fps > 0 else 30.0, frames=max(n, 1), dur=dur,
                        sw=int(st.get("width") or 0),
                        sh=int(st.get("height") or 0))
            info["color"] = self._prev_sample_color(video)
        except Exception as ex:
            info["err"] = str(ex)
        self._prev_q.put(("probe", seq, info))

    def _prev_sample_color(self, video):
        """Average the four frame corners — usually the backdrop colour."""
        try:
            vf = ("split=4[a][b][c][d];"
                  "[a]crop=iw/8:ih/8:0:0[x1];"
                  "[b]crop=iw/8:ih/8:iw-iw/8:0[x2];"
                  "[c]crop=iw/8:ih/8:0:ih-ih/8[x3];"
                  "[d]crop=iw/8:ih/8:iw-iw/8:ih-ih/8[x4];"
                  "[x1][x2][x3][x4]hstack=4,scale=1:1[o]")
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", "0", "-i", video,
                 "-frames:v", "1", "-filter_complex", vf, "-map", "[o]",
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                capture_output=True, env=clean_env(), timeout=60)
            if r.returncode == 0 and len(r.stdout) >= 3:
                b = r.stdout[:3]
                return "#%02x%02x%02x" % (b[0], b[1], b[2])
        except Exception:
            pass
        return None

    def _prev_probe_done(self, seq, info):
        if seq != self._prev_probe_seq:
            return
        if info["err"]:
            self.prev_status.config(text=f"ffprobe failed: {info['err']}",
                                    fg=ERROR)
            return
        self.prev_fps = info["fps"]
        self.prev_frames = info["frames"]
        self.prev_dur = info["dur"]
        self.prev_src_w = info.get("sw") or 0
        self.prev_src_h = info.get("sh") or 0
        self.prev_scale.config(to=max(1, self.prev_frames - 1))
        self.prev_frame.set(min(int(self.prev_frame.get()),
                                self.prev_frames - 1))
        col = info.get("color")
        if col and self._prev_color_auto:
            self._prev_color_setting = True
            try:
                self.chroma_color.set(col)
                self.color_swatch.config(bg=col)
                for sw in self._swatches:
                    sw.config(bg=col)
            except Exception:
                pass
            finally:
                self._prev_color_setting = False
        self._prev_update_time()
        extra = f"  ·  bg {col}" if col else ""
        self.prev_status.config(
            text=f"{self.prev_frames} frames  ·  {self.prev_fps:.2f} fps  ·  "
                 f"{self.prev_dur:.2f} s{extra}", fg=SUBTEXT)
        self._prev_rendered = False
        self._prev_request(force=True)

    # ── rendering (worker thread) ─────────────────────────────────────────
    def _prev_worker(self, video, idx, cfg, seq):
        t0 = time.time()
        try:
            tmp = os.path.join(tempfile.gettempdir(), "tvm_preview")
            os.makedirs(tmp, exist_ok=True)
            orig = os.path.join(tmp, "orig.png")
            res = os.path.join(tmp, "res.png")
            t = idx / cfg["fps"]
            w, h = cfg["w"], cfg["h"]
            self._prev_frame_cmd(video, t, orig, None, w, h)
            if cfg["mode"] == "chroma":
                self._prev_frame_cmd(
                    video, t, res,
                    (cfg["color"], cfg["sim"], cfg["blend"]), w, h)
            else:
                self._prev_ai_frame(video, t, tmp, res, cfg["model"], w, h)
            dt = time.time() - t0
            self._prev_q.put(("ok", seq, orig, res, dt, idx, cfg["mode"], w, h))
        except Exception as ex:
            self._prev_q.put(("err", seq, str(ex)))

    def _prev_checker(self, W, H):
        return (f"color=c={self.CHECK_BG}:s={W}x{H}:d=1,"
                f"drawgrid=w=32:h=32:t=16:c={self.CHECK_FG}[bg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto[o]")

    def _prev_frame_cmd(self, video, t, out, chroma, W, H):
        # Input seeking (-ss before -i) leaves the decoded frame's PTS at the
        # seek position; overlay then waits for a frame at t=0, emits the bare
        # checkerboard and -frames:v 1 ends the run -> empty previews.  Rebase
        # the PTS so the first frame is always t=0.
        fg = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              "setpts=PTS-STARTPTS,")
        if chroma:
            col, sim, blend = chroma
            hexv = col.lstrip("#").upper()
            fg += (f"format=rgba,chromakey=0x{hexv}:{sim:.3f}:{blend:.3f}")
        else:
            fg += "format=rgb24"
        graph = f"[0:v]{fg}[fg];" + self._prev_checker(W, H)
        cmd = ["ffmpeg", "-y", "-ss", f"{t:.4f}", "-i", video,
               "-frames:v", "1", "-filter_complex", graph,
               "-map", "[o]", "-frames:v", "1", "-pix_fmt", "rgb24", out]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=clean_env(), timeout=120)
        if r.returncode != 0 or not os.path.isfile(out):
            raise RuntimeError("ffmpeg frame render failed: "
                               + (r.stderr or "")[-300:])

    def _prev_ai_frame(self, video, t, tmp, res, model, W, H):
        fdir = os.path.join(tmp, "in")
        odir = os.path.join(tmp, "out")
        for d in (fdir, odir):
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
        cut = os.path.join(fdir, "frame0001.png")
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.4f}", "-i", video,
             "-frames:v", "1",
             "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease",
             "-pix_fmt", "rgb24", cut],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=clean_env(), timeout=120)
        if r.returncode != 0 or not os.path.isfile(cut):
            raise RuntimeError("frame extraction failed: "
                               + (r.stderr or "")[-300:])
        cmd = [REMBG_EXE or "rembg", "p"]
        if model and model != "u2net":
            cmd += ["-m", model]
        cmd += [fdir, odir]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=clean_env(), timeout=900)
        cut_out = os.path.join(odir, "frame0001.png")
        if r.returncode != 0 or not os.path.isfile(cut_out):
            raise RuntimeError("rembg failed: "
                               + ((r.stderr or r.stdout or "")[-300:]))
        graph = ("[1:v]drawgrid=w=32:h=32:t=16:c=" + self.CHECK_FG + "[bg];"
                 "[bg][0:v]overlay=(W-w)/2:(H-h)/2:format=auto[o]")
        cmd = ["ffmpeg", "-y", "-i", cut_out, "-f", "lavfi",
               "-i", f"color=c={self.CHECK_BG}:s={W}x{H}:d=1",
               "-filter_complex", graph, "-map", "[o]",
               "-frames:v", "1", "-pix_fmt", "rgb24", res]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=clean_env(), timeout=120)
        if r.returncode != 0 or not os.path.isfile(res):
            raise RuntimeError("composite failed: " + (r.stderr or "")[-300:])

    def _prev_show(self, seq, orig, res, dt, idx, mode, w, h):
        try:
            if seq == self._pv_seq:
                for cv, path in ((self.prev_cv_orig, orig),
                                 (self.prev_cv_res, res)):
                    img = tk.PhotoImage(file=path)
                    cv.delete("all")
                    cx = max(cv.winfo_width(), w) // 2
                    cy = max(cv.winfo_height(), h) // 2
                    cv.create_image(cx, cy, image=img)
                    cv.image = img
                self._prev_rendered = True
                kind = "AI" if mode == "ai" else "chroma"
                self.prev_status.config(
                    text=f"frame {idx + 1}  ·  {kind}  ·  "
                         f"{dt * 1000:.0f} ms  ·  {w}x{h}", fg=SUCCESS)
        except Exception as ex:
            self.prev_status.config(text=f"✗  cannot display image: {ex}",
                                    fg=ERROR)
        finally:
            self._pv_release()

    def _prev_fail(self, seq, msg):
        if seq == self._pv_seq:
            self.prev_status.config(text=f"✗  {msg}", fg=ERROR)
        self._pv_release()

    # ── main-thread queue drain ───────────────────────────────────────────
    def _prev_poll(self):
        try:
            while True:
                msg = self._prev_q.get_nowait()
                self._prev_dispatch(msg)
        except Exception:
            pass
        try:
            self.after(80, self._prev_poll)
        except Exception:
            pass

    def _prev_dispatch(self, msg):
        kind = msg[0]
        if kind == "probe":
            self._prev_probe_done(msg[1], msg[2])
        elif kind == "ok":
            self._prev_show(*msg[1:])
        elif kind == "err":
            self._prev_fail(msg[1], msg[2])
