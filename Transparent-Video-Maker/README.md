# Transparent Video Maker

A desktop GUI tool for converting videos with solid-color backgrounds into
true transparent videos with an alpha channel (`.webm` or `.mov`).

Built around **ffmpeg** (chroma key removal) and **rembg** (AI background removal),
with a simple point-and-click interface — and a matching command-line mode with
a live progress bar for batch work or remote shells.

> **用途：用于去 AI 视频的绿幕。**
> 把 AI 生成视频里的纯色背景（绿幕 / 蓝幕 / 黑底 / 白底）抠成带 alpha 的透明视频，
> 供上级仓库 `aboutme-pets` 的精灵图转换脚本使用。本目录原为独立仓库，已并入
> 上级仓库并移除其 `.git`，上游提交历史不再保留；Windows 启动器
> （`start-tvm.bat`）与 Windows 专用分支已移除，仅保留 macOS / Linux 路径。

---

## Features

- **Chroma Key removal** — precise color-based removal for solid-color backgrounds
  (green screen, blue screen, black, white, or any custom color)
- **AI removal** — rembg-powered background removal for more complex backgrounds,
  with multiple model options including a character/figure-optimized model
- **Frames → Video** tab — encode a folder of manually-edited transparent PNG frames
  into a transparent video (useful if you edit frames yourself in Photoshop, etc.)
- Output to **WebM/VP9** (best for web, OBS, most editors) or **MOV/ProRes 4444**
  (high-quality editing master)
- Live log output and step-by-step progress indicators
- **CLI mode with a terminal progress bar** — same pipeline without the GUI
  (`video` / `frames` subcommands), suitable for batch jobs and SSH sessions
- Auto-detects frame filename patterns

---

## Requirements

### 1. Python 3.10+ with Tk

macOS ships Tk with the system `python3`. With a Homebrew Python, install the
matching `python-tk`, e.g. `brew install python-tk@3.11`.

### 2. ffmpeg

```bash
brew install ffmpeg
```

*ffmpeg is a separate program licensed under GPL v3. It is not included in this
repository. See [ffmpeg.org](https://ffmpeg.org) for more information.*

### 3. rembg (Python package)

```bash
python3 -m pip install "rembg[cli]"
```

*rembg is licensed under the MIT License.*

---

## Installation

1. Install Python, ffmpeg, and rembg as described above.
2. Run the app from this directory:

```bash
cd Transparent-Video-Maker
python3 TransparentVideoMaker.py
```

The app looks for `rembg` on `PATH`; override the binary with the `REMBG_EXE`
environment variable if it lives somewhere unusual.

---

## Command line (with progress bar)

Running the script with no arguments opens the GUI; any argument switches to the
terminal interface, which needs no Tk:

```bash
# video → transparent video (chroma key, the default)
python3 TransparentVideoMaker.py video -i clip.mp4

# AI removal, custom output path
python3 TransparentVideoMaker.py video -i clip.mp4 --method ai -m isnet-general-use -o out.webm

# blue screen → ProRes 4444 master
python3 TransparentVideoMaker.py video -i clip.mp4 --color '#0047bb' --format mov

# PNG sequence (already transparent) → video
python3 TransparentVideoMaker.py frames -i ./frames --fps 30 -o out.webm

# batch: every video in a folder (add -r for subfolders) → ./clips_out
python3 TransparentVideoMaker.py video -i ./clips -o ./clips_out -r

# AI green screens are rarely pure green: sample the real colour, cap each
# file at 1.5 MB, convert three at a time
python3 TransparentVideoMaker.py video -i ./clips -o ./clips_out \
    --color auto --target-size 1.5M --jobs 3

# every option
python3 TransparentVideoMaker.py video --help
```

Each step draws a single-line progress bar on stderr:

```
▶ 2/3  chroma key  (0x00FF00, similarity 0.1, blend 0.05)
chroma key        [██████████████████████] 100%  60/60f  45.8x  ETA 00:00  done
```

It shows the step, percentage, processed seconds (`s`) or frames (`f`), speed and
ETA, and drops detail (ETA, speed, counts) on narrow terminals so the line never
wraps. The bar is only drawn when stderr is a terminal, so redirected logs stay
clean.

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--dry-run` | print the ffmpeg/rembg commands without running them |
| `-q`, `--quiet` | no bar and no chatter (the output path still goes to stdout) |
| `--keep-frames` | keep the extracted / keyed temporary frame folders |
| `--overwrite` | replace an existing output file |
| `--workdir DIR` | where temporary frame folders go (default: output folder) |
| `--fps N` | output frame rate (default: the source rate) |
| `-r`, `--recursive` | with a folder input, also scan subfolders |
| `--color auto` | read the backdrop colour from the clip instead of `#00ff00` |
| `--target-size 1.5M` | aim for that file size (webm only, two-pass) |
| `--jobs N` | batch mode: convert N videos in parallel (default 1) |

When `-i` is a **folder**, every video in it (`.mp4 .mov .m4v .webm .mkv .avi
.wmv .flv .mpg .mpeg .ts .m2ts .mts .3gp`) is converted to
`<name>_transparent.<fmt>` inside `-o` (which must then be a folder and defaults
to the input folder). Existing outputs are skipped unless `--overwrite`; a file
that fails is reported and the batch keeps going; the exit code is `1` if any
file failed. Files already ending in `_transparent` are ignored, so re-running
in the same folder is safe.

`--target-size` turns the WebM encoder into a two-pass VBR run and derives the
bitrate from the clip length (`≈ size × 8 / seconds / 2`, because VP9 keeps
transparency in a second stream that shares the `-b:v` budget). If the first
attempt still overshoots, the file is re-encoded once at a lower bitrate, so
the cap is normally honoured; sizes slightly below the target are expected.
`--jobs N` converts N files of a batch in parallel — each worker gets its own
temp folders and progress lines are printed as files finish.

Notes:

- The result path is printed on **stdout**; all messages go to **stderr**, so
  `out=$(python3 TransparentVideoMaker.py video -i clip.mp4 -q)` works.
- Temporary folders are `<base>_frames` and `<base>_transparent` next to the
  output, and are deleted at the end unless `--keep-frames` is given.
- `ffprobe` may report `yuv420p` for the WebM: VP9 stores alpha as a separate
  stream, so verify with `ffmpeg -c:v libvpx-vp9 -i out.webm -frames:v 1 -pix_fmt rgba check.png`
  (players and editors read it correctly).

---

## Quick Start

### Full Pipeline (video in → transparent video out)

1. Open the **Full Pipeline** tab.
2. Click **Browse** and select your video file.
3. Choose a removal method:
   - **Chroma Key** — recommended if your background is a solid color.
     Pick the background color (use the presets or color picker),
     then adjust **Similarity** and **Edge Blend** as needed.
   - **AI Model** — use if the background is complex or unknown.
4. Choose your output format (WebM recommended for most uses).
5. Click **▶ RUN FULL PIPELINE**.

### Frames → Video (manually edited frames → transparent video)

1. Open the **Frames → Video** tab.
2. Click **Browse** and select the folder containing your transparent PNG frames.
3. Click **Auto-detect** to detect the filename pattern automatically.
4. Set your framerate and output format.
5. Click **▶ ENCODE VIDEO FROM FRAMES**.

### Tips for best chroma key results

- Keep **Similarity** low (0.05–0.15) to avoid eating into the character's edges.
- Raise **Edge Blend** slightly (0.03–0.08) if the outline looks jagged or harsh.
- A clean, evenly-lit solid background will give the sharpest result.

---

## License

This project (the Python GUI script) is released under the **MIT License**.
See [LICENSE](LICENSE) for details.

**Third-party tools** (not included — must be installed separately):
- **ffmpeg** — GPL v3 — https://ffmpeg.org
- **rembg** — MIT — https://github.com/danielgatis/rembg
