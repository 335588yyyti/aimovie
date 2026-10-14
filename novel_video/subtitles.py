"""字幕時間軸計算與 SRT / ASS 輸出。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .text import Sentence, clean_line, speak_len


@dataclass
class Cue:
    start: float
    end: float
    text: str


def cues_for_sentence(sentence: Sentence, start: float, duration: float) -> list[Cue]:
    """把一句話的朗讀時間依字數比例分配給它的每一行字幕。"""
    weights = [speak_len(ln) for ln in sentence.lines]
    total = sum(weights) or 1
    cues: list[Cue] = []
    t = start
    for ln, w in zip(sentence.lines, weights):
        d = duration * w / total
        cues.append(Cue(t, t + d, clean_line(ln)))
        t += d
    return cues


def proportional_timeline(sentences: list[Sentence], total: float, gap: float = 0.0) -> list[tuple[float, float]]:
    """沒有逐句音檔時（使用外部音檔），依字數估算每句的起訖時間。"""
    weights = [speak_len(s.text) for s in sentences]
    speech = max(0.0, total - gap * max(0, len(sentences) - 1))
    unit = speech / (sum(weights) or 1)
    out = []
    t = 0.0
    for w in weights:
        d = w * unit
        out.append((t, d))
        t += d + gap
    return out


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def write_srt(cues: list[Cue], path: Path) -> None:
    blocks = [
        f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n"
        for i, c in enumerate(cues, 1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")


@dataclass
class SubtitleStyle:
    font: str = "Noto Sans CJK TC"
    font_size: int = 0  # 0 = 依影片高度自動
    color: str = "FFFFFF"  # RRGGBB
    outline_color: str = "000000"
    outline: float = 3.0
    shadow: float = 1.0
    bold: bool = True
    position: str = "bottom"  # bottom / center / top
    margin_v: int = 0  # 0 = 自動


def _ass_color(rrggbb: str) -> str:
    rrggbb = rrggbb.lstrip("#")
    r, g, b = rrggbb[0:2], rrggbb[2:4], rrggbb[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def write_ass(
    cues: list[Cue], path: Path, width: int, height: int, style: SubtitleStyle, title: str = ""
) -> None:
    base = min(width, height)
    size = style.font_size or int(base * 0.065)
    align = {"bottom": 2, "center": 5, "top": 8}[style.position]
    margin_v = style.margin_v or int(height * (0.12 if height > width else 0.08))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.font},{size},{_ass_color(style.color)},&H000000FF,{_ass_color(style.outline_color)},&H80000000,{-1 if style.bold else 0},0,0,0,100,100,0,0,1,{style.outline},{style.shadow},{align},{int(width * 0.05)},{int(width * 0.05)},{margin_v},1
Style: Title,{style.font},{int(size * 0.8)},{_ass_color(style.color)},&H000000FF,{_ass_color(style.outline_color)},&H80000000,-1,0,0,0,100,100,0,0,1,{style.outline},{style.shadow},8,{int(width * 0.05)},{int(width * 0.05)},{int(height * 0.06)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [
        f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,{_ass_escape(c.text)}"
        for c in cues
    ]
    if title and cues:
        events.insert(
            0, f"Dialogue: 1,{_ass_time(0)},{_ass_time(cues[-1].end + 5)},Title,,0,0,0,,{_ass_escape(title)}"
        )
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
