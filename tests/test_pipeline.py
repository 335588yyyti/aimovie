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


async def fake_synth_words(text, out, cfg):
    # 假的逐詞時間點：每個可唸的字 0.1 秒，回傳 (開始, 長度, 文字)
    await fake_synth(text, out, cfg)
    spoken = [c for c in text if tts._speak(c)]
    return [(i * 0.1, 0.1, c) for i, c in enumerate(spoken)]


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
    monkeypatch.setattr(tts, "_edge_synthesize_words", fake_synth_words)
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


def test_intro_cover_and_looping_background(tmp_path):
    novel = tmp_path / "story.txt"
    novel.write_text(NOVEL, encoding="utf-8")
    audio = tmp_path / "voice.mp3"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=330:duration=5", str(audio)])
    bg = tmp_path / "bg.mp4"  # 比旁白短，需要循環
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=180x320:rate=25:duration=2", str(bg)])
    cover = tmp_path / "cover.jpg"
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=red:s=640x360", "-frames:v", "1", str(cover)])
    out = tmp_path / "out"

    rc = cli.main([str(novel), "-o", str(out), "--audio", str(audio), "--bg", str(bg),
                   "--size", "640x360", "--bg-fit", "pad", "--preset", "ultrafast", "--tail", "0.5",
                   "--intro", str(cover), "--intro-duration", "1.5"])
    assert rc == 0
    video = out / "story.mp4"
    assert probe_duration(video) == pytest.approx(7.0, abs=0.15)
    # 第 0.5 秒應該是紅色封面
    frame = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(video), "-frames:v", "1",
         "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    r, g, b = frame[:3]
    assert r > 200 and g < 60 and b < 60


def test_align_sentences_uses_word_timings():
    sents = ["你好，世界。", "第二句！"]
    words = [(0.0, 0.2, "你好"), (0.3, 0.2, "世界"), (1.0, 0.2, "第二"), (1.2, 0.1, "句")]
    tl = tts.align_sentences(sents, words, 1.5)
    assert tl[0] == (0.0, pytest.approx(1.0))
    assert tl[1] == (pytest.approx(1.0), pytest.approx(0.5))
    # 對不上時改用字數比例
    tl = tts.align_sentences(sents, [(0.0, 0.1, "別的字")], 1.2)
    assert tl[0][1] == pytest.approx(1.2 * 4 / 7)
    assert tl[1][0] + tl[1][1] == pytest.approx(1.2)


def test_make_chunks_isolates_titles_and_limits_size():
    texts = ["第一章", "一二三。", "四五六。", "七八九。", "第二章", "十。"]
    chunks = tts.make_chunks(texts, {0, 4}, max_chars=8)
    assert chunks == [[0], [1, 2], [3], [4], [5]]
