"""命令列入口：小說文字 → 旁白配音 + 字幕 + 背景影片 → MP4。"""
from __future__ import annotations

import argparse
import random
import re
import shutil
import sys
from pathlib import Path

from .ffmpeg import (
    AUDIO_EXTS, VideoConfig, collect_media, prepare_background, probe_duration, render_video, require_ffmpeg,
    run_ffmpeg,
)
from .subtitles import Cue, SubtitleStyle, cues_for_sentence, proportional_timeline, write_ass, write_srt
from .text import Chapter, build_chapters
from .tts import TTSConfig, synthesize_narration

SIZES = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "square": (1080, 1080)}


def parse_size(value: str) -> tuple[int, int]:
    if value in SIZES:
        return SIZES[value]
    m = re.fullmatch(r"(\d+)[xX*](\d+)", value)
    if not m:
        raise argparse.ArgumentTypeError("尺寸格式：vertical / horizontal / square 或 1080x1920")
    w, h = int(m.group(1)), int(m.group(2))
    return w - w % 2, h - h % 2


def parse_range(value: str) -> set[int]:
    """'1,3,5-8' → {1,3,5,6,7,8}"""
    out: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")
    return s[:40] or "video"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="novel_video",
        description="自動把小說文字剪成影片：AI 配音 + 自動字幕 + 背景影片 + 背景音樂",
    )
    p.add_argument("input", nargs="?", type=Path, help="小說文字檔（UTF-8 .txt）")
    p.add_argument("-o", "--output", type=Path, default=Path("output"), help="輸出資料夾（預設 output/）")

    g = p.add_argument_group("素材")
    g.add_argument("--bg", type=Path, action="append", default=[],
                   help="背景影片/圖片，或放素材的資料夾；可重複指定。多個素材會隨機串接循環")
    g.add_argument("--bgm", type=Path, help="背景音樂檔，或資料夾（隨機挑一首）")
    g.add_argument("--audio", type=Path,
                   help="使用現成的旁白音檔（不做 TTS），字幕會依字數比例對齊到整段音檔")

    g = p.add_argument_group("配音（edge-tts）")
    g.add_argument("--voice", default="zh-TW-HsiaoChenNeural",
                   help="聲音，例如 zh-TW-HsiaoChenNeural、zh-TW-YunJheNeural、zh-CN-YunxiNeural")
    g.add_argument("--rate", default="+0%", help="語速，例如 +20%%、-10%%")
    g.add_argument("--pitch", default="+0Hz", help="音調，例如 +5Hz")
    g.add_argument("--gap", type=float, default=0.25, help="句與句之間的停頓秒數")
    g.add_argument("--concurrency", type=int, default=4, help="同時合成幾句")
    g.add_argument("--list-voices", action="store_true", help="列出可用的中文聲音後結束")

    g = p.add_argument_group("字幕")
    g.add_argument("--max-chars", type=int, default=0, help="每行字幕最多幾個字（預設直式 14、橫式 22）")
    g.add_argument("--font", default="Noto Sans CJK TC", help="字幕字型名稱")
    g.add_argument("--fonts-dir", type=Path, help="額外字型資料夾（放 .ttf/.otf）")
    g.add_argument("--font-size", type=int, default=0, help="字幕字級（0 = 自動）")
    g.add_argument("--sub-color", default="FFFFFF", help="字幕顏色 RRGGBB")
    g.add_argument("--outline-color", default="000000", help="字幕描邊顏色 RRGGBB")
    g.add_argument("--sub-position", choices=["bottom", "center", "top"], default="bottom")
    g.add_argument("--title", default="", help="顯示在畫面上方的書名（會加上章節名）")

    g = p.add_argument_group("影片")
    g.add_argument("--size", type=parse_size, default=SIZES["vertical"],
                   help="vertical(1080x1920) / horizontal(1920x1080) / square / 自訂 WxH")
    g.add_argument("--fps", type=int, default=30)
    g.add_argument("--bg-fit", choices=["crop", "pad"], default="crop",
                   help="背景比例不同時：crop 裁切填滿；pad 等比縮小完整顯示，兩側補黑")
    g.add_argument("--dim", type=float, default=0.35, help="背景變暗程度 0~1")
    g.add_argument("--bgm-volume", type=float, default=0.12, help="背景音樂音量（相對旁白）")
    g.add_argument("--crf", type=int, default=23, help="畫質（越小越好，檔案越大）")
    g.add_argument("--preset", default="veryfast", help="x264 速度 preset")
    g.add_argument("--tail", type=float, default=1.0, help="旁白結束後多留幾秒")

    g = p.add_argument_group("章節")
    g.add_argument("--no-chapters", action="store_true", help="不偵測章節，整篇做成一支影片")
    g.add_argument("--merge", action="store_true", help="偵測章節但全部合成一支影片")
    g.add_argument("--chapters", type=parse_range, help="只做指定章節，例如 1-3,5")
    g.add_argument("--keep-work", action="store_true", help="保留中間檔（音檔、ASS）")
    g.add_argument("--seed", type=int, help="背景隨機排列的種子")
    return p


def list_voices() -> None:
    import asyncio

    import edge_tts

    voices = asyncio.run(edge_tts.list_voices())
    for v in sorted(voices, key=lambda v: v["ShortName"]):
        if v["Locale"].startswith("zh"):
            print(f"{v['ShortName']:<36} {v['Gender']:<7} {v['Locale']}")


def make_video(
    name: str,
    chapter: Chapter,
    args: argparse.Namespace,
    work_root: Path,
    bgm: Path | None,
) -> Path:
    work = work_root / name
    work.mkdir(parents=True, exist_ok=True)
    width, height = args.size
    sentences = chapter.sentences

    if args.audio:
        narration = args.audio
        audio_len = probe_duration(narration)
        timeline = proportional_timeline(sentences, audio_len, gap=0.0)
    else:
        narration = work / "narration.wav"
        cfg = TTSConfig(voice=args.voice, rate=args.rate, pitch=args.pitch, concurrency=args.concurrency)
        timeline = synthesize_narration(
            [s.text for s in sentences], narration, work_root / "tts_cache", cfg, gap=args.gap
        )
        audio_len = timeline[-1][0] + timeline[-1][1]

    cues: list[Cue] = []
    for s, (start, dur) in zip(sentences, timeline):
        cues.extend(cues_for_sentence(s, start, dur))

    out_mp4 = args.output / f"{name}.mp4"
    write_srt(cues, args.output / f"{name}.srt")
    if not args.audio:
        # 另存旁白音檔，方便拿去其他剪輯軟體使用
        run_ffmpeg(["-i", str(narration), "-c:a", "libmp3lame", "-b:a", "192k", str(args.output / f"{name}.mp3")])
    style = SubtitleStyle(
        font=args.font, font_size=args.font_size, color=args.sub_color,
        outline_color=args.outline_color, position=args.sub_position,
    )
    title = " ".join(x for x in (args.title, chapter.title) if x)
    ass = work / "subs.ass"
    write_ass(cues, ass, width, height, style, title=title)

    duration = audio_len + args.tail
    vcfg = VideoConfig(
        width=width, height=height, fps=args.fps, dim=args.dim, crf=args.crf,
        preset=args.preset, bgm_volume=args.bgm_volume, fit=args.bg_fit,
    )
    bg_input = prepare_background(args.bg, duration, work_root, vcfg)
    print(f"  合成影片（{duration:.1f} 秒）…")
    render_video(bg_input, narration, ass, out_mp4, duration, vcfg, bgm=bgm, fonts_dir=args.fonts_dir)
    return out_mp4


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_voices:
        list_voices()
        return 0
    if not args.input:
        build_parser().error("請指定小說文字檔")
    require_ffmpeg()
    if args.seed is not None:
        random.seed(args.seed)

    raw = args.input.read_text(encoding="utf-8-sig", errors="replace")
    width, height = args.size
    max_chars = args.max_chars or (14 if height > width else 22)
    chapters = build_chapters(raw, max_chars=max_chars, detect_chapters=not args.no_chapters)
    if not chapters:
        print("文字檔裡沒有可朗讀的內容", file=sys.stderr)
        return 1

    if args.chapters:
        chapters = [c for i, c in enumerate(chapters, 1) if i in args.chapters]
    # 外部音檔只能對應一段完整文字，所以合成一支
    if args.merge or args.audio:
        merged = Chapter(title="")
        for c in chapters:
            merged.sentences.extend(c.sentences)
        chapters = [merged]

    bgm = None
    if args.bgm:
        tracks = collect_media([args.bgm], AUDIO_EXTS)
        bgm = random.choice(tracks) if tracks else None

    args.output.mkdir(parents=True, exist_ok=True)
    work_root = args.output / "_work"
    stem = safe_name(args.input.stem)
    results = []
    for i, ch in enumerate(chapters, 1):
        name = stem if len(chapters) == 1 else f"{stem}_{i:03d}_{safe_name(ch.title or str(i))}"
        print(f"[{i}/{len(chapters)}] {ch.title or stem}（{len(ch.sentences)} 句）")
        results.append(make_video(name, ch, args, work_root, bgm))

    if not args.keep_work:
        # 保留 TTS 快取，下次重跑同樣文字就不必再合成
        for p in work_root.iterdir():
            if p.name != "tts_cache":
                shutil.rmtree(p) if p.is_dir() else p.unlink()

    print("完成：")
    for r in results:
        print(f"  {r}")
    return 0
