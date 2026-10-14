# aimovie — 小說自動剪輯影片

把小說文字檔自動做成影片，整個流程一行指令完成：

1. **自動切章節／句子**（偵測「第X章」、「序章」、「番外」等標題）
2. **AI 配音**：用 [edge-tts](https://github.com/rany2/edge-tts) 逐句合成旁白（免費、不用 API key）
3. **自動字幕**：依每句實際配音長度對齊時間軸，長句自動在逗號處斷行，燒進畫面，也另外輸出 `.srt`
4. **背景影片**：可放單支影片、圖片或整個資料夾，素材會自動裁切成目標比例、隨機串接並循環到旁白結束；背景會稍微變暗讓字幕更清楚
5. **背景音樂**：自動循環、壓低音量墊在旁白底下，結尾淡出

## 安裝

需要 Python 3.10+ 與 [ffmpeg](https://ffmpeg.org/)（要有 libass，一般發行版都有）。

```bash
pip install -r requirements.txt
```

字幕預設字型是 `Noto Sans CJK TC`，沒安裝的話會自動改用系統裡的其他中文字型；也可以用 `--font` / `--fonts-dir` 指定。

## 使用方式

```bash
# 最簡單：直式短影音（1080x1920），每章一支影片
python -m novel_video 我的小說.txt --bg assets/backgrounds --bgm assets/bgm

# 橫式影片、男聲、語速加快 15%、畫面上方顯示書名
python -m novel_video 我的小說.txt --size horizontal --voice zh-TW-YunJheNeural \
    --rate +15% --title "《我的小說》" --bg assets/backgrounds

# 只做第 1~3 章，合成一支
python -m novel_video 我的小說.txt --chapters 1-3 --merge --bg bg.mp4

# 已經有錄好的旁白：跳過 TTS，字幕依字數比例對齊
python -m novel_video 我的小說.txt --audio 旁白.mp3 --bg bg.mp4

# 列出可用的中文聲音
python -m novel_video --list-voices
```

輸出在 `output/`：每章一支 `.mp4` 與對應的 `.srt`。配音會快取在 `output/_work/tts_cache/`，修改字幕樣式後重跑不必重新合成。

### 素材資料夾

```
assets/
  backgrounds/   放背景影片（mp4/mov/mkv/webm…）或圖片（jpg/png…）
  bgm/           放背景音樂（mp3/wav/m4a…），每次隨機挑一首
```

## 常用參數

| 參數 | 說明 | 預設 |
| --- | --- | --- |
| `--bg` | 背景影片／圖片／資料夾，可重複指定 | 純色背景 |
| `--bgm` | 背景音樂檔或資料夾 | 無 |
| `--audio` | 使用現成旁白音檔，不做 TTS | — |
| `--voice` | edge-tts 聲音 | `zh-TW-HsiaoChenNeural` |
| `--rate` / `--pitch` | 語速／音調，例如 `+20%`、`+5Hz` | `+0%` / `+0Hz` |
| `--gap` | 句與句的停頓秒數 | `0.25` |
| `--size` | `vertical` / `horizontal` / `square` / `WxH` | `vertical` |
| `--max-chars` | 每行字幕最多字數 | 直式 14、橫式 22 |
| `--font` / `--font-size` | 字幕字型／字級 | 自動 |
| `--sub-color` / `--outline-color` | 字幕顏色／描邊顏色（RRGGBB） | 白字黑邊 |
| `--sub-position` | `bottom` / `center` / `top` | `bottom` |
| `--title` | 畫面上方顯示的書名（會接章節名） | 無 |
| `--dim` | 背景變暗程度 0~1 | `0.35` |
| `--bgm-volume` | 背景音樂音量 | `0.12` |
| `--no-chapters` / `--merge` / `--chapters` | 章節處理方式 | 每章一支 |

完整說明：`python -m novel_video -h`

## 測試

```bash
pip install pytest
python -m pytest
```

測試用正弦波代替真的 TTS，並用 ffmpeg 產生假背景，會實際跑完整個剪輯流程。
