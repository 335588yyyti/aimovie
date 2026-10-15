"""小說文字處理：章節切分、句子切分、字幕斷行。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CHAPTER_RE = re.compile(
    r"^\s*(第\s*[0-9０-９零〇一二三四五六七八九十百千萬万两兩]+\s*[章回節节卷集話话篇].*|"
    r"(序章|楔子|尾聲|尾声|番外|後記|后记)\b.*)$"
)
# 句末標點（含後面緊跟的引號）
SENTENCE_END_RE = re.compile(r"(.+?(?:[。！？!?…]+|\.{3,})[」』”’\"')）]*)")
# 可以斷開字幕的次要標點
SOFT_BREAK_CHARS = "，,、；;：:—"
# 字幕行首尾要去掉的標點
STRIP_PUNCT = "，,。、；;：:"
PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass
class Sentence:
    """一句要送去 TTS 的文字，以及它拆出來的字幕行。"""

    text: str
    lines: list[str] = field(default_factory=list)
    is_title: bool = False
    chapter_start: bool = False  # 每章內文的第一句


@dataclass
class Chapter:
    title: str
    sentences: list[Sentence] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.sentences)


def normalize(raw: str) -> str:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").replace("﻿", "")
    lines = [ln.strip().strip("　") for ln in raw.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def split_chapters(raw: str, detect: bool = True) -> list[tuple[str, str]]:
    """回傳 [(章節標題, 內文)]。沒偵測到章節標題時整篇當成一章。"""
    text = normalize(raw)
    if not detect:
        return [("", text)]
    chapters: list[tuple[str, list[str]]] = []
    for line in text.split("\n"):
        if CHAPTER_RE.match(line) and len(line) <= 40:
            chapters.append((line, []))
        else:
            if not chapters:
                chapters.append(("", []))
            chapters[-1][1].append(line)
    result = [(title, "\n".join(body)) for title, body in chapters]
    return [(t, b) for t, b in result if t or b.strip()]


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        pos = 0
        for m in SENTENCE_END_RE.finditer(para):
            sentences.append(m.group(1).strip())
            pos = m.end()
        rest = para[pos:].strip()
        if rest:
            sentences.append(rest)
    return [s for s in sentences if has_speakable(s)]


def has_speakable(s: str) -> bool:
    return bool(PUNCT_RE.sub("", s))


def speak_len(s: str) -> int:
    """用來估算朗讀時間的字數（不算標點和空白）。"""
    return max(1, len(PUNCT_RE.sub("", s)))


def wrap_subtitle(sentence: str, max_chars: int) -> list[str]:
    """把一句話拆成多行字幕：優先在逗號等處斷開，太長再硬切。"""
    # 先依次要標點切成小片段（標點留在片段尾端）
    pieces: list[str] = []
    buf = ""
    for ch in sentence:
        buf += ch
        if ch in SOFT_BREAK_CHARS:
            pieces.append(buf)
            buf = ""
    if buf:
        pieces.append(buf)

    # 合併過短的片段，避免字幕一閃而過
    lines: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(clean_line(cur + p)) > max_chars:
            lines.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        lines.append(cur)

    # 單片段仍超長就硬切，盡量平均分配
    out: list[str] = []
    for ln in lines:
        c = clean_line(ln)
        if len(c) <= max_chars:
            out.append(ln)
            continue
        n = -(-len(ln) // max_chars)
        size = -(-len(ln) // n)
        out.extend(ln[i : i + size] for i in range(0, len(ln), size))
    return [ln for ln in out if has_speakable(ln)]


def clean_line(line: str) -> str:
    """字幕顯示用：去掉頭尾的逗號句號等。"""
    return line.strip().strip(STRIP_PUNCT).strip()


def _is_book_title(title: str, body: str, has_chapters: bool) -> bool:
    """章節之前只有一行、沒有句末標點的短文字，視為書名。"""
    return (
        not title and has_chapters and "\n" not in body.strip()
        and len(body.strip()) <= 40 and not SENTENCE_END_RE.fullmatch(body.strip())
    )


def build_chapters(
    raw: str, max_chars: int = 18, detect_chapters: bool = True, read_titles: bool = True
) -> list[Chapter]:
    """read_titles=False 時不唸書名和章節標題，故事直接一路講下去。"""
    chapters: list[Chapter] = []
    parts = split_chapters(raw, detect_chapters)
    has_chapters = any(t for t, _ in parts)
    for title, body in parts:
        ch = Chapter(title=title)
        if _is_book_title(title, body, has_chapters):
            if read_titles:
                ch.sentences.append(Sentence(text=body.strip(), lines=wrap_subtitle(body.strip(), max_chars), is_title=True))
                chapters.append(ch)
            continue
        if title and read_titles:
            ch.sentences.append(Sentence(text=title, lines=wrap_subtitle(title, max_chars), is_title=True))
        for i, s in enumerate(split_sentences(body)):
            ch.sentences.append(Sentence(text=s, lines=wrap_subtitle(s, max_chars), chapter_start=(i == 0)))
        if ch.sentences:
            chapters.append(ch)
    return chapters
