"""文字轉語音（edge-tts），並拼接成一條旁白音軌。

兩種模式：
- flow（預設）：連續幾句一起合成，語調連貫；用逐字時間點對齊每句字幕。
- sentence：每句分開合成，句間插入固定停頓。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import ssl
import wave
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import run_ffmpeg
from .text import PUNCT_RE

SAMPLE_RATE = 24000


@dataclass
class TTSConfig:
    voice: str = "zh-TW-HsiaoChenNeural"
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    concurrency: int = 4
    retries: int = 3


def _use_extra_ca() -> None:
    """edge-tts 只信任 certifi 的憑證；有設定 SSL_CERT_FILE（例如公司代理）時一併信任。"""
    ca = os.environ.get("SSL_CERT_FILE")
    if not ca or not os.path.exists(ca):
        return
    import certifi
    from edge_tts import communicate

    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=ca)
    communicate._SSL_CTX = ctx


async def _edge_synthesize(text: str, out: Path, cfg: TTSConfig) -> None:
    import edge_tts  # 延遲匯入：只用外部音檔時不需要安裝

    _use_extra_ca()
    last_err: Exception | None = None
    for attempt in range(cfg.retries):
        try:
            comm = edge_tts.Communicate(text, cfg.voice, rate=cfg.rate, pitch=cfg.pitch, volume=cfg.volume)
            tmp = out.with_suffix(".part")
            await comm.save(str(tmp))
            if tmp.stat().st_size == 0:
                raise RuntimeError("edge-tts 回傳空音檔")
            tmp.replace(out)
            return
        except Exception as e:  # 網路錯誤時重試
            last_err = e
            await asyncio.sleep(2**attempt)
    raise RuntimeError(f"TTS 失敗：{text[:30]}… ({last_err})")


def _cache_name(text: str, cfg: TTSConfig) -> str:
    key = "|".join([cfg.voice, cfg.rate, cfg.pitch, cfg.volume, text])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


async def _synthesize_all(texts: list[str], cache_dir: Path, cfg: TTSConfig, synth) -> list[Path]:
    sem = asyncio.Semaphore(cfg.concurrency)
    done = 0

    async def one(text: str) -> Path:
        nonlocal done
        mp3 = cache_dir / f"{_cache_name(text, cfg)}.mp3"
        if not mp3.exists():
            async with sem:
                await synth(text, mp3, cfg)
        done += 1
        print(f"\r  TTS {done}/{len(texts)}", end="", flush=True)
        return mp3

    paths = await asyncio.gather(*(one(t) for t in texts))
    print()
    return list(paths)


def _to_pcm(src: Path) -> bytes:
    wav = src.with_suffix(".wav")
    if not wav.exists():
        run_ffmpeg(["-i", str(src), "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", str(wav)])
    with wave.open(str(wav), "rb") as w:
        return w.readframes(w.getnframes())


def synthesize_narration(
    texts: list[str],
    out_wav: Path,
    cache_dir: Path,
    cfg: TTSConfig,
    gap: float = 0.25,
    synth=None,
) -> list[tuple[float, float]]:
    """逐句合成並拼接。回傳每句 (開始秒數, 長度秒數)，用來對齊字幕。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    mp3s = asyncio.run(_synthesize_all(texts, cache_dir, cfg, synth or _edge_synthesize))

    silence = b"\x00\x00" * int(SAMPLE_RATE * gap)
    timeline: list[tuple[float, float]] = []
    t = 0.0
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for i, mp3 in enumerate(mp3s):
            pcm = _to_pcm(mp3)
            dur = len(pcm) / 2 / SAMPLE_RATE
            timeline.append((t, dur))
            w.writeframes(pcm)
            t += dur
            if i < len(mp3s) - 1:
                w.writeframes(silence)
                t += gap
    return timeline


# ---------- flow 模式 ----------

Words = list[tuple[float, float, str]]  # (開始秒數, 長度秒數, 文字)


async def _edge_synthesize_words(text: str, out: Path, cfg: TTSConfig) -> Words:
    """合成一段文字，同時取得每個詞的時間點。"""
    import edge_tts

    _use_extra_ca()
    last_err: Exception | None = None
    for attempt in range(cfg.retries):
        try:
            comm = edge_tts.Communicate(
                text, cfg.voice, rate=cfg.rate, pitch=cfg.pitch, volume=cfg.volume, boundary="WordBoundary"
            )
            audio = bytearray()
            words: Words = []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    words.append((chunk["offset"] / 1e7, chunk["duration"] / 1e7, chunk["text"]))
            if not audio:
                raise RuntimeError("edge-tts 回傳空音檔")
            out.write_bytes(bytes(audio))
            return words
        except Exception as e:
            last_err = e
            await asyncio.sleep(2**attempt)
    raise RuntimeError(f"TTS 失敗：{text[:30]}… ({last_err})")


def _speak(s: str) -> str:
    return PUNCT_RE.sub("", s)


def align_sentences(sentences: list[str], words: Words | None, total: float) -> list[tuple[float, float]]:
    """用逐詞時間點算出每句的 (開始, 長度)；對不上時改用字數比例估算。"""
    lens = [max(1, len(_speak(s))) for s in sentences]
    spoken = "".join(_speak(s) for s in sentences)
    starts: list[float] = []
    if words:
        char_start: list[float] = []
        for off, _dur, w in words:
            char_start += [off] * len(_speak(w))
        if "".join(_speak(w) for _o, _d, w in words) == spoken:
            pos = 0
            for n in lens:
                starts.append(char_start[pos] if pos < len(char_start) else total)
                pos += n
    if not starts:
        unit = total / sum(lens)
        acc = 0.0
        for n in lens:
            starts.append(acc)
            acc += n * unit
        starts[0] = 0.0
    starts[0] = 0.0
    ends = starts[1:] + [total]
    return [(a, max(0.05, b - a)) for a, b in zip(starts, ends)]


def make_chunks(texts: list[str], breaks: set[int], max_chars: int) -> list[list[int]]:
    """把連續句子組成一段一段；breaks 裡的句子（例如章節標題）自成一段。"""
    chunks: list[list[int]] = []
    cur: list[int] = []
    size = 0
    for i, t in enumerate(texts):
        if cur and (i in breaks or (i - 1) in breaks or size + len(t) > max_chars):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(t)
    if cur:
        chunks.append(cur)
    return chunks


def synthesize_flowing(
    texts: list[str],
    out_wav: Path,
    cache_dir: Path,
    cfg: TTSConfig,
    breaks: set[int] | None = None,
    max_chars: int = 300,
    gap: float = 0.35,
    title_gap: float = 0.9,
    synth=None,
) -> list[tuple[float, float]]:
    """連續句子一起合成（語調較自然）。回傳每句 (開始秒數, 長度秒數)。"""
    breaks = breaks or set()
    synth = synth or _edge_synthesize_words
    cache_dir.mkdir(parents=True, exist_ok=True)
    chunks = make_chunks(texts, breaks, max_chars)
    sem = asyncio.Semaphore(cfg.concurrency)
    done = 0

    async def one(idx: list[int]) -> tuple[Path, Words | None]:
        nonlocal done
        text = "".join(texts[i] for i in idx)
        name = _cache_name("flow|" + text, cfg)
        mp3, meta = cache_dir / f"{name}.mp3", cache_dir / f"{name}.json"
        if not mp3.exists():
            async with sem:
                words = await synth(text, mp3, cfg)
            if words is not None:
                meta.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        done += 1
        print(f"\r  TTS {done}/{len(chunks)} 段", end="", flush=True)
        words = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else None
        return mp3, words

    async def run_all():
        return await asyncio.gather(*(one(c) for c in chunks))

    results = asyncio.run(run_all())
    print()

    timeline: list[tuple[float, float]] = [(0.0, 0.0)] * len(texts)
    t = 0.0
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for ci, (idx, (mp3, words)) in enumerate(zip(chunks, results)):
            pcm = _to_pcm(mp3)
            dur = len(pcm) / 2 / SAMPLE_RATE
            for i, (a, d) in zip(idx, align_sentences([texts[i] for i in idx], words, dur)):
                timeline[i] = (t + a, d)
            w.writeframes(pcm)
            t += dur
            if ci < len(chunks) - 1:
                nxt = chunks[ci + 1][0]
                pause = title_gap if (idx[-1] in breaks or nxt in breaks) else gap
                w.writeframes(b"\x00\x00" * int(SAMPLE_RATE * pause))
                t += pause
    return timeline
