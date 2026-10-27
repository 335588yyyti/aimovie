"""分鏡表：從 storyboard.json 產生分鏡網頁（提示詞＋旁白）、場景清單與配音稿。

用法：
    python -m novel_video.storyboard novels/xxx_storyboard.json --html out.html
    python -m novel_video.storyboard novels/xxx_storyboard.json --scenes dir/scenes.txt --script dir/script.txt
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def prompt_for(sb: dict, shot: dict) -> str:
    chars = {c["name"]: c["desc"] for c in sb["characters"]}
    cast = "；".join(f"{n}：{chars[n]}" for n in shot.get("cast", []) if n in chars)
    parts = [sb["style"]]
    if cast:
        parts.append(f"角色外觀：{cast}。")
    parts += [f"畫面：{shot['scene']}", sb["suffix"]]
    return "\n".join(parts)


def setup_message(sb: dict) -> str:
    lines = [
        "接下來我會請你畫一系列插圖，全部使用相同的畫風與角色設定，請保持角色長相一致。",
        f"畫風：{sb['style']}",
        f"規格：{sb['suffix']}",
        "角色：",
    ]
    lines += [f"- {c['name']}：{c['desc']}" for c in sb["characters"]]
    lines.append("我每次給你一格分鏡，你畫一張。")
    return "\n".join(lines)


def scenes_txt(sb: dict, ext: str = ".jpg") -> str:
    """場景清單：每格分鏡用它旁白的第一句當換圖時間點。"""
    out = [f"# {sb['title']} 場景清單（由分鏡表產生）"]
    for shot in sb["shots"]:
        first = shot["text"].split("。")[0].split("！")[0].split("？")[0]
        out.append(f"{shot['id']}{ext} | {first[:20]}")
    return "\n".join(out) + "\n"


def script_txt(sb: dict) -> str:
    """給剪映配音用的完整旁白稿（依分鏡順序）。"""
    return "\n\n".join(shot["text"] for shot in sb["shots"]) + "\n"


def render_html(sb: dict) -> str:
    e = html.escape
    cast_cards = "".join(
        f'<div><b>{e(c["name"])}</b><span class="note">{e(c.get("note", ""))}</span>{e(c["desc"])}</div>'
        for c in sb["characters"]
    )
    shots = []
    for s in sb["shots"]:
        p = prompt_for(sb, s)
        shots.append(f"""
<section class="card" id="{e(s['id'])}">
  <header><span class="file">{e(s['id'])}</span><h3>{e(s['title'])}</h3></header>
  <div class="label">旁白／台詞</div>
  <p class="text">{e(s['text'])}</p>
  <div class="label">生圖提示詞</div>
  <p class="prompt">{e(p)}</p>
  <div class="actions"><button type="button" class="copy" data-copy="prompt">複製提示詞</button>
  <button type="button" class="copy ghost" data-copy="text">複製旁白</button></div>
</section>""")
    setup = setup_message(sb)
    script = script_txt(sb)
    return f"""<title>{e(sb['title'])}分鏡表</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@600;900&family=Noto+Sans+TC:wght@400;500;700&display=swap">
<style>
/* 版面：單欄分鏡清單；每格分鏡一張卡，旁白在上、提示詞在下 */
:root {{
  --bg: #f4f2ef; --surface: #ffffff; --ink: #221e1b; --muted: #6c645d; --line: #e3ddd6;
  --gold: #9a6b14; --gold-soft: #f5ead3; --ink-soft: #efeae4;
  --font-display: "Noto Serif TC", "Songti TC", "PMingLiU", serif;
  --font-body: "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #15120f; --surface: #1f1a16; --ink: #efe9e2; --muted: #a89e94; --line: #352d26;
    --gold: #e3b45a; --gold-soft: #3a2c16; --ink-soft: #2a231d; color-scheme: dark;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #15120f; --surface: #1f1a16; --ink: #efe9e2; --muted: #a89e94; --line: #352d26;
  --gold: #e3b45a; --gold-soft: #3a2c16; --ink-soft: #2a231d; color-scheme: dark;
}}
body {{ background: var(--bg); color: var(--ink); font-family: var(--font-body); font-size: 16px; line-height: 1.7; }}
.wrap {{ max-width: 800px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 32px 64px; }}
h1 {{ font-family: var(--font-display); font-weight: 900; font-size: clamp(26px, 6vw, 38px); line-height: 1.3; margin: 0 0 6px; text-wrap: balance; }}
.lead {{ color: var(--muted); margin: 0 0 20px; }}
h2 {{ font-family: var(--font-display); font-weight: 600; font-size: 21px; margin: 32px 0 10px; }}
.cast {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 10px; }}
.cast div {{ background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; font-size: 14px; min-width: 0; }}
.cast b {{ font-size: 15px; color: var(--gold); margin-right: 6px; }}
.note {{ font-size: 12px; color: var(--muted); display: block; margin-bottom: 2px; }}
.box {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }}
.box p {{ margin: 0 0 10px; font-size: 14px; white-space: pre-wrap; word-break: break-word; }}
.cards {{ display: grid; gap: 14px; }}
.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; min-width: 0; }}
.card header {{ display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }}
.file {{ font-family: ui-monospace, "SF Mono", Consolas, monospace; font-weight: 700; background: var(--gold-soft); color: var(--gold); border-radius: 6px; padding: 0 8px; }}
.card h3 {{ margin: 0; font-size: 17px; }}
.label {{ font-size: 12px; font-weight: 700; letter-spacing: .08em; color: var(--muted); margin-top: 6px; }}
.text {{ margin: 2px 0 8px; font-size: 16px; }}
.prompt {{ margin: 2px 0 10px; font-size: 13px; background: var(--ink-soft); border-radius: 8px; padding: 10px 12px; white-space: pre-wrap; word-break: break-word; }}
.actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
button.copy {{ font: inherit; font-size: 14px; font-weight: 700; cursor: pointer; border-radius: 8px; padding: 6px 14px;
  background: var(--gold); color: var(--surface); border: 1px solid var(--gold); }}
button.copy.ghost {{ background: transparent; color: var(--gold); }}
button.copy.done {{ background: var(--ink); border-color: var(--ink); color: var(--surface); }}
button:focus-visible {{ outline: 2px solid var(--gold); outline-offset: 2px; }}
</style>
<div class="wrap">
  <h1>《{e(sb['title'])}》分鏡表</h1>
  <p class="lead">共 {len(sb['shots'])} 格分鏡。先把「開場設定」貼給繪圖 AI，再依序貼每格的提示詞；圖片用分鏡編號命名（例如 {e(sb['shots'][0]['id'])}.jpg）。</p>

  <h2>角色設定</h2>
  <div class="cast">{cast_cards}</div>

  <h2>開場設定（每個繪圖對話先貼一次）</h2>
  <div class="box"><p id="setup">{e(setup)}</p>
  <button type="button" class="copy" data-target="setup">複製開場設定</button></div>

  <h2>配音稿（剪映用，整段貼上）</h2>
  <div class="box"><p id="script">{e(script)}</p>
  <button type="button" class="copy" data-target="script">複製配音稿</button></div>

  <h2>分鏡</h2>
  <div class="cards">{''.join(shots)}</div>
</div>
<script>
(function () {{
  function copy(btn, el) {{
    var label = btn.textContent;
    function ok() {{ btn.textContent = "已複製"; btn.classList.add("done");
      setTimeout(function () {{ btn.textContent = label; btn.classList.remove("done"); }}, 1500); }}
    function fallback() {{ var r = document.createRange(); r.selectNodeContents(el);
      var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); btn.textContent = "已選取，請手動複製"; }}
    try {{ navigator.clipboard.writeText(el.textContent).then(ok, fallback); }} catch (e) {{ fallback(); }}
  }}
  document.querySelectorAll("button.copy").forEach(function (b) {{
    b.addEventListener("click", function () {{
      var el = b.dataset.target ? document.getElementById(b.dataset.target)
        : b.closest(".card").querySelector(b.dataset.copy === "text" ? ".text" : ".prompt");
      copy(b, el);
    }});
  }});
}})();
</script>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="從分鏡表 JSON 產生分鏡網頁、場景清單與配音稿")
    ap.add_argument("storyboard", type=Path)
    ap.add_argument("--html", type=Path, help="輸出分鏡網頁")
    ap.add_argument("--scenes", type=Path, help="輸出場景清單（給 --scenes 用）")
    ap.add_argument("--script", type=Path, help="輸出配音稿文字檔（也可當小說輸入）")
    args = ap.parse_args(argv)
    sb = load(args.storyboard)
    if args.html:
        args.html.write_text(render_html(sb), encoding="utf-8")
    if args.scenes:
        args.scenes.write_text(scenes_txt(sb), encoding="utf-8")
    if args.script:
        args.script.write_text(script_txt(sb), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
