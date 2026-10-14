"""ffmpeg 相關：探測長度、準備背景影片、合成最終影片。"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"找不到 {tool}，請先安裝 ffmpeg 並加入 PATH")


def run_ffmpeg(args: list[str], cwd: Path | None = None) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失敗：\n{' '.join(cmd)}\n{proc.stderr}")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def collect_media(sources: list[Path], exts: set[str]) -> list[Path]:
    files: list[Path] = []
    for src in sources:
        if src.is_dir():
            files.extend(sorted(p for p in src.iterdir() if p.suffix.lower() in exts))
        elif src.exists():
            files.append(src)
        else:
            raise SystemExit(f"找不到檔案：{src}")
    return files


@dataclass
class VideoConfig:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    dim: float = 0.35  # 背景變暗程度 0~1，讓字幕更清楚
    crf: int = 23
    preset: str = "veryfast"
    bgm_volume: float = 0.12
    narration_volume: float = 1.0
    fade: float = 0.6
    shuffle: bool = True


def _fit_filter(cfg: VideoConfig) -> str:
    w, h = cfg.width, cfg.height
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,fps={cfg.fps}"
    )


def prepare_background(sources: list[Path], duration: float, work: Path, cfg: VideoConfig) -> list[str]:
    """回傳背景影片的 ffmpeg 輸入參數（會自動循環到足夠長度）。"""
    files = collect_media(sources, VIDEO_EXTS | IMAGE_EXTS)
    if not files:
        # 沒有背景素材就用純色背景
        return ["-f", "lavfi", "-i", f"color=c=0x101418:s={cfg.width}x{cfg.height}:r={cfg.fps}"]

    if len(files) == 1:
        f = files[0]
        if f.suffix.lower() in IMAGE_EXTS:
            return ["-loop", "1", "-framerate", str(cfg.fps), "-i", str(f)]
        return ["-stream_loop", "-1", "-i", str(f)]

    # 多個素材：先統一解析度/幀率，再隨機排列串接直到長度足夠
    clip_dir = work / "bg_clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    clips: list[tuple[Path, float]] = []
    for i, f in enumerate(files):
        out = clip_dir / f"clip_{i:03d}_{cfg.width}x{cfg.height}.mp4"
        if not out.exists():
            print(f"  整理背景素材 {f.name}")
            src = ["-loop", "1", "-t", "8", "-i", str(f)] if f.suffix.lower() in IMAGE_EXTS else ["-i", str(f)]
            run_ffmpeg([
                *src, "-an", "-vf", _fit_filter(cfg),
                "-c:v", "libx264", "-preset", cfg.preset, "-crf", "20", "-pix_fmt", "yuv420p", str(out),
            ])
        clips.append((out, probe_duration(out)))

    order: list[Path] = []
    total = 0.0
    while total < duration + 1:
        batch = clips[:]
        if cfg.shuffle:
            random.shuffle(batch)
        for path, d in batch:
            order.append(path)
            total += d
            if total >= duration + 1:
                break
    concat = work / "bg_concat.txt"
    concat.write_text("".join(f"file '{p.resolve()}'\n" for p in order), encoding="utf-8")
    return ["-f", "concat", "-safe", "0", "-i", str(concat)]


def _filter_path(p: Path) -> str:
    """ffmpeg filter 參數裡的路徑跳脫。"""
    s = str(p.resolve()).replace("\\", "/")
    return s.replace(":", "\\:").replace("'", "\\'")


def render_video(
    bg_input: list[str],
    narration: Path,
    ass: Path,
    out: Path,
    duration: float,
    cfg: VideoConfig,
    bgm: Path | None = None,
    fonts_dir: Path | None = None,
) -> None:
    vf = [_fit_filter(cfg)]
    if cfg.dim > 0:
        vf.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{cfg.dim:.2f}:t=fill")
    ass_opt = f"ass=filename='{_filter_path(ass)}'"
    if fonts_dir:
        ass_opt += f":fontsdir='{_filter_path(fonts_dir)}'"
    vf.append(ass_opt)
    if cfg.fade > 0:
        vf.append(f"fade=t=in:st=0:d={cfg.fade}")
        vf.append(f"fade=t=out:st={max(0.0, duration - cfg.fade):.3f}:d={cfg.fade}")
    filters = [f"[0:v]{','.join(vf)}[v]"]

    narr = f"[1:a]aresample=48000,volume={cfg.narration_volume},apad[narr]"
    inputs = [*bg_input, "-i", str(narration)]
    if bgm:
        inputs += ["-stream_loop", "-1", "-i", str(bgm)]
        filters += [
            narr,
            f"[2:a]aresample=48000,volume={cfg.bgm_volume}[bgm]",
            "[narr][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]",
        ]
        last = "mix"
    else:
        filters.append(narr)
        last = "narr"
    if cfg.fade > 0:
        filters.append(f"[{last}]afade=t=out:st={max(0.0, duration - cfg.fade):.3f}:d={cfg.fade}[a]")
    else:
        filters.append(f"[{last}]anull[a]")

    run_ffmpeg([
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(out),
    ])
