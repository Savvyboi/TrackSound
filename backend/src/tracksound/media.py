"""Finding and running ffmpeg, so users don't have to install it.

TrackSound reads any audio or video file through ffmpeg. It uses, in order:

1. ``$TRACKSOUND_FFMPEG`` (and ``$TRACKSOUND_FFPROBE``) if set;
2. ``ffmpeg`` / ``ffprobe`` on the PATH;
3. the ffmpeg binary that ships inside the ``imageio-ffmpeg`` package (a dependency), which
   has no ffprobe: file information then comes from parsing ``ffmpeg -i`` output.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    """Path of an ffmpeg executable."""
    env = os.environ.get("TRACKSOUND_FFMPEG")
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("ffmpeg was not found. Install ffmpeg, or reinstall TrackSound (it normally brings its own).") from e


@lru_cache(maxsize=1)
def ffprobe_exe() -> str | None:
    """Path of ffprobe, or None (the bundled ffmpeg comes without it)."""
    env = os.environ.get("TRACKSOUND_FFPROBE")
    if env is not None:
        return env or None
    if os.environ.get("TRACKSOUND_FFMPEG"):
        sib = Path(os.environ["TRACKSOUND_FFMPEG"]).with_name("ffprobe" + Path(os.environ["TRACKSOUND_FFMPEG"]).suffix)
        return str(sib) if sib.exists() else None
    return shutil.which("ffprobe")


def probe(path: str | Path) -> dict:
    """ffprobe-style information: ``{"format": {...}, "streams": [...]}``. Uses ffprobe when
    available, else parses ``ffmpeg -i``. Raises ValueError if the file can't be read."""
    exe = ffprobe_exe()
    if exe:
        r = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                           capture_output=True, text=True, errors="replace", timeout=120)
        if r.returncode != 0:
            raise ValueError(f"could not read the file: {r.stderr.strip()[:300]}")
        return json.loads(r.stdout or "{}")
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)],
                       capture_output=True, text=True, errors="replace", timeout=120)
    return parse_ffmpeg_info(r.stderr)


_INPUT = re.compile(r"^Input #0, (.+), from '")
_DURATION = re.compile(r"^\s*Duration: (?:(\d+):(\d+):([\d.]+)|N/A)")
_STREAM = re.compile(r"^\s*Stream #\d+:(\d+)(?:\[[^\]]*\])?(?:\([^)]*\))?: (Video|Audio|Subtitle|Data|Attachment): (\S+)(.*)$")
_META_ITEM = re.compile(r"^(\s*)(\S[^:]*?)\s*: (.*)$")
_CHANNELS = {"mono": 1, "stereo": 2, "2.1": 3, "quad": 4, "4.0": 4, "5.0": 5, "5.1": 6, "6.1": 7, "7.1": 8}


def parse_ffmpeg_info(text: str) -> dict:
    """The parts of ffprobe's output TrackSound needs, from ``ffmpeg -i`` stderr."""
    lines = text.splitlines()
    fmt: dict = {"tags": {}}
    streams: list[dict] = []
    found = False
    meta_target: dict | None = None
    meta_indent = 0
    for line in lines:
        m = _INPUT.match(line)
        if m:
            found = True
            fmt["format_name"] = m.group(1)
            continue
        if not found:
            continue
        if line.strip() == "Metadata:":
            meta_target = fmt["tags"] if not streams else streams[-1].setdefault("tags", {})
            meta_indent = len(line) - len(line.lstrip()) + 2
            continue
        if meta_target is not None:
            mi = _META_ITEM.match(line)
            if mi and len(mi.group(1)) >= meta_indent:
                meta_target[mi.group(2).strip()] = mi.group(3).strip()
                continue
            meta_target = None
        m = _DURATION.match(line)
        if m:
            if m.group(1) is not None:
                fmt["duration"] = str(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)))
            st = re.search(r"start: ([-\d.]+)", line)
            if st:
                fmt["start_time"] = st.group(1)
            continue
        m = _STREAM.match(line)
        if m:
            kind, codec, rest = m.group(2).lower(), m.group(3).rstrip(","), m.group(4)
            s = {"index": int(m.group(1)), "codec_type": kind, "codec_name": codec,
                 "disposition": {"attached_pic": 1 if "(attached pic)" in rest else 0}}
            if kind == "audio":
                hz = re.search(r"(\d+) Hz", rest)
                if hz:
                    s["sample_rate"] = hz.group(1)
                ch = re.search(r"(\d+) channels", rest)
                if ch:
                    s["channels"] = int(ch.group(1))
                else:
                    for name, n in _CHANNELS.items():
                        if re.search(rf",\s*{re.escape(name)}(\(|,|\s|$)", rest):
                            s["channels"] = n
                            break
            if kind == "video":
                fps = re.search(r"([\d.]+) fps", rest)
                if fps:
                    s["avg_frame_rate"] = f"{fps.group(1)}/1"
            streams.append(s)
    if not found:
        tail = next((ln.strip() for ln in reversed(lines) if ln.strip()), "unknown format")
        raise ValueError(f"could not read the file: {tail[:300]}")
    return {"format": fmt, "streams": streams}
