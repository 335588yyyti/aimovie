"""文字轉語音：逐句合成（edge-tts），並拼接成一條旁白音軌。"""
from __future__ import annotations

import asyncio
import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import run_ffmpeg

SAMPLE_RATE = 24000


@dataclass
class TTSConfig:
    voice: str = "zh-TW-HsiaoChenNeural"
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    concurrency: int = 4
    retries: int = 3


async def _edge_synthesize(text: str, out: Path, cfg: TTSConfig) -> None:
    import edge_tts  # 延遲匯入：只用外部音檔時不需要安裝

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
