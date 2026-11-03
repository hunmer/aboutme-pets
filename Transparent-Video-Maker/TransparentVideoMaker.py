"""Transparent Video Maker — entry point.

Run with no arguments for the Tk GUI, or use the terminal interface:

    python3 TransparentVideoMaker.py --help
    python3 TransparentVideoMaker.py video -i clip.mp4 --method chroma
    python3 TransparentVideoMaker.py frames -i ./frames --fps 30 -o out.webm

The application is split into the `tvm` package:

    tvm/theme.py          colours, fonts and small Tk widget factories
    tvm/tools.py          ffmpeg/rembg discovery + child-process environment
    tvm/encode.py         ffmpeg command builders shared by the GUI and CLI
    tvm/app.py            window shell, shared state and helpers
    tvm/pipeline.py       "Full Pipeline" tab: settings + live preview + run
    tvm/preview.py        throttled one-frame matte preview
    tvm/frames2video.py   "Frames → Video" tab
    tvm/cli.py            terminal interface with a progress bar (no Tk)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        from tvm.app import App
        App().mainloop()
        return 0
    from tvm.cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
