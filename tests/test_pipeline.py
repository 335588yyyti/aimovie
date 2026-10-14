import json
import subprocess
from pathlib import Path

import pytest

from novel_video import cli, tts
from novel_video.ffmpeg import probe_duration, run_ffmpeg
from novel_video.subtitles import cues_for_sentence, proportional_timeline
from novel_video.text import build_chapters, clean_line, split_sentences, wrap_subtitle

NOVEL = """第一章 雨夜
那天晚上下著大雨，街上一個人也沒有。
「你終於來了。」她低聲說道，眼神裡帶著一絲說不清的情緒！

第二章 重逢
三年後，他們在車站再次相遇……誰也沒有先開口。
"""


def test_split_chapters_and_sentences():
    chapters = build_chapters(NOVEL, max_chars=14)
    assert [c.title for c in chapters] == ["第一章 雨夜", "第二章 重逢"]
    texts = [s.text for s in chapters[0].sentences]
    assert texts == [
        "第一章 雨夜",
        "那天晚上下著大雨，街上一個人也沒有。",
        "「你終於來了。」",
        "她低聲說道，眼神裡帶著一絲說不清的情緒！",
    ]
    assert split_sentences("三年後……誰也沒有開口") == ["三年後……", "誰也沒有開口"]


def test_wrap_subtitle_respects_max_chars():
    s = "這是一個非常非常長而且完全沒有任何標點符號的句子用來測試硬切功能是否正常"
    lines = wrap_subtitle(s, 10)
    assert "".join(lines) == s
    assert all(len(clean_line(ln)) <= 10 for ln in lines)
    assert wrap_subtitle("那天晚上下著大雨，街上一個人也沒有。", 14) == ["那天晚上下著大雨，", "街上一個人也沒有。"]


def test_cue_timing_is_contiguous():
    sent = build_chapters("那天晚上下著大雨，街上一個人也沒有。", 14, detect_chapters=False)[0].sentences[0]
    cues = cues_for_sentence(sent, 2.0, 4.0)
    assert cues[0].start == 2.0 and cues[-1].end == pytest.approx(6.0)
    assert cues[0].text == "那天晚上下著大雨"
    tl = proportional_timeline([sent, sent], 10.0, gap=1.0)
    assert tl[1][0] + tl[1][1] == pytest.approx(10.0)


async def fake_synth(text, out, cfg):
    # 用正弦波代替真的 TTS：每個字 0.1 秒
    dur = 0.1 * len(text)
    run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}", str(out)])


def _make_bg(dir_: Path):
    dir_.mkdir()
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=2", str(dir_ / "a.mp4")])
    run_ffmpeg(["-f", "lavfi", "-i", "mandelbrot=size=320x320:rate=25", "-t", "1.5", str(dir_ / "b.mp4")])


def _stream_info(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def test_end_to_end_with_tts(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "_edge_synthesize", fake_synth)
    novel = tmp_path / "novel.txt"
    novel.write_text(NOVEL, encoding="utf-8")
    _make_bg(tmp_path / "bg")
    bgm = tmp_path / "bgm.mp3"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=220:duration=3", str(bgm)])
    out = tmp_path / "out"

    rc = cli.main([
        str(novel), "-o", str(out), "--bg", str(tmp_path / "bg"), "--bgm", str(bgm),
        "--size", "360x640", "--title", "測試小說", "--seed", "1", "--preset", "ultrafast",
    ])
    assert rc == 0
    videos = sorted(out.glob("*.mp4"))
    assert [v.name for v in videos] == ["novel_001_第一章_雨夜.mp4", "novel_002_第二章_重逢.mp4"]
    info = _stream_info(videos[0])
    assert (info["video"]["width"], info["video"]["height"]) == (360, 640)
    assert "audio" in info
    srt = (out / "novel_001_第一章_雨夜.srt").read_text(encoding="utf-8")
    assert "街上一個人也沒有" in srt
    # 影片長度 = 旁白 + 結尾留白
    assert probe_duration(videos[0]) > 6


def test_end_to_end_with_existing_audio(tmp_path):
    novel = tmp_path / "story.txt"
    novel.write_text(NOVEL, encoding="utf-8")
    audio = tmp_path / "voice.mp3"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=330:duration=5", str(audio)])
    img = tmp_path / "bg.png"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=400x300", "-frames:v", "1", str(img)])
    out = tmp_path / "out"

    rc = cli.main([str(novel), "-o", str(out), "--audio", str(audio), "--bg", str(img),
                   "--size", "horizontal", "--bg-fit", "pad", "--preset", "ultrafast", "--tail", "0.5"])
    assert rc == 0
    assert probe_duration(out / "story.mp4") == pytest.approx(5.5, abs=0.15)
    assert not (out / "_work" / "story").exists()
