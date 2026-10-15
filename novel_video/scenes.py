"""場景插圖：讀取場景清單，依旁白時間算出每張圖顯示多久。

清單格式（UTF-8 文字檔，一行一個場景，# 開頭為註解）：

    01.jpg | 我閉關一萬年
    02.jpg | 「啪！」

「|」左邊是圖片檔（相對於清單檔所在資料夾），右邊是故事裡的一小段原文，
那句話唸到時就換成這張圖。第一張圖一律從頭開始顯示。
"""
from __future__ import annotations

from pathlib import Path

from .text import PUNCT_RE, Sentence


def _norm(s: str) -> str:
    return PUNCT_RE.sub("", s)


def load_scenes(path: Path) -> list[tuple[Path, str]]:
    scenes = []
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            raise SystemExit(f"{path.name} 第 {n} 行格式錯誤，應為「圖片檔 | 故事原文」：{line}")
        img, anchor = (x.strip() for x in line.split("|", 1))
        img_path = (path.parent / img).resolve()
        if not img_path.exists():
            raise SystemExit(f"{path.name} 第 {n} 行：找不到圖片 {img}")
        scenes.append((img_path, anchor))
    if not scenes:
        raise SystemExit(f"{path.name} 裡沒有任何場景")
    return scenes


def scene_durations(
    scenes: list[tuple[Path, str]],
    sentences: list[Sentence],
    timeline: list[tuple[float, float]],
    total: float,
) -> list[float]:
    """依每個場景的原文位置，算出每張圖要顯示幾秒（加總等於 total）。"""
    starts = [0.0]
    pos = 0
    for img, anchor in scenes[1:]:
        key = _norm(anchor)
        for i in range(pos, len(sentences)):
            if key and key in _norm(sentences[i].text):
                starts.append(timeline[i][0])
                pos = i + 1
                break
        else:
            raise SystemExit(f"場景 {img.name}：在故事裡找不到「{anchor}」（或順序在前一個場景之前）")
    ends = starts[1:] + [total]
    return [max(0.5, b - a) for a, b in zip(starts, ends)]
