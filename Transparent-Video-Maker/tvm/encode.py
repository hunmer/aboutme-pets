"""ffmpeg command builders shared by the GUI tabs and the CLI."""
import os


def encode_cmd(frames_dir, fps, crf, fmt, outfile,
               pattern="frame%04d.png", extra=(), bitrate=None,
               pass_no=None, passlog=None):
    """Build the ffmpeg command that encodes a PNG sequence to a transparent video.

    `extra` is spliced in right after the binary name so callers can add global
    flags such as ``-progress pipe:1``.  Passing `bitrate` (bits/s) switches the
    webm encoder to two-pass VBR: `pass_no=1` writes nothing and `pass_no=2`
    writes `outfile`, both sharing `passlog`.
    """
    inp = os.path.join(frames_dir, pattern)
    if fmt != "webm":
        return ["ffmpeg", *extra, "-y", "-framerate", str(fps), "-i", inp,
                "-c:v", "prores_ks", "-profile:v", "4444",
                "-pix_fmt", "yuva444p10le", outfile]

    cmd = ["ffmpeg", *extra, "-y", "-framerate", str(fps), "-i", inp,
           "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-row-mt", "1"]
    if bitrate:
        cmd += ["-b:v", str(int(bitrate))]
        if pass_no:
            cmd += ["-pass", str(pass_no), "-passlogfile", str(passlog)]
    else:
        cmd += ["-crf", str(crf), "-b:v", "0"]
    if pass_no == 1:
        cmd += ["-f", "null", "-"]
    else:
        cmd.append(outfile)
    return cmd
