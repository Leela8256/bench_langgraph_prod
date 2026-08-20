"""Frame extraction. Pure computation — no fastapi, no langgraph imports.

Mirrors RocketRide's frame_grabber (interval profile): the engine converts
interval=15 s to fps=1/15 and samples via an ffmpeg-based reader. Same here:
ffmpeg's fps filter, lossless PNG intermediates (JPEG would perturb the
pixels the detector sees), decoded to RGB.

Verified against the engine on ES2016d: both yield 102 frames for 1522 s.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

INTERVAL_S = 15


def ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg  # bundles a static ffmpeg — same trick the engine uses
    return imageio_ffmpeg.get_ffmpeg_exe()


def extract_frames(video_path: str) -> list[Image.Image]:
    """One frame per INTERVAL_S seconds, as RGB PIL images, in time order."""
    with tempfile.TemporaryDirectory(prefix="lgframes_") as td:
        out = Path(td)
        subprocess.run(
            [ffmpeg_bin(), "-nostdin", "-loglevel", "error",
             "-i", video_path,
             "-vf", f"fps=1/{INTERVAL_S}",
             "-f", "image2", str(out / "f_%06d.png")],
            check=True)
        frames = []
        for p in sorted(out.glob("f_*.png")):
            with Image.open(p) as im:
                frames.append(im.convert("RGB").copy())
    return frames
