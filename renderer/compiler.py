from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ir_validator import validate_ir

_PLAYER_JS = (Path(__file__).parent / "player.js").read_text(encoding="utf-8")

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
*, *::before, *::after {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: 100%; height: 100%; background: #000; overflow: hidden; font-family: {font_family}, "Microsoft YaHei", "PingFang SC", sans-serif; }}

#mg-container {{
  position: relative;
  width: 1280px; height: 720px;
  margin: auto;
  overflow: hidden;
  background: {bg_color};
}}
body {{ display: flex; flex-direction: column; align-items: center; justify-content: center; }}

.mg-scene {{
  position: absolute; inset: 0;
  opacity: 0; pointer-events: none;
  overflow: hidden;
}}
.mg-scene.active {{ opacity: 1; pointer-events: auto; }}

.mg-element {{
  position: absolute;
  opacity: 0;
  will-change: transform, opacity;
}}
.mg-element img {{
  width: 100%; height: 100%;
  object-fit: cover; display: block;
}}
.mg-element.type-background {{ z-index: 0; }}
.mg-element.type-image {{ z-index: 1; }}
.mg-element.type-shape {{ z-index: 1; }}
.mg-element.type-icon {{ z-index: 2; }}
.mg-element.type-text {{ z-index: 3; }}

.text-title {{ font-weight: 700; text-align: center; line-height: 1.2; }}
.text-subtitle {{ font-weight: 600; text-align: center; line-height: 1.3; }}
.text-body {{ font-weight: 400; text-align: left; line-height: 1.6; }}
.text-label {{ font-weight: 500; text-align: center; line-height: 1.4; }}
.text-number-highlight {{ font-weight: 800; text-align: center; line-height: 1.0; font-variant-numeric: tabular-nums; }}

#mg-subtitle {{
  position: absolute; bottom: 30px; left: 50%; transform: translateX(-50%);
  max-width: 90%; padding: 8px 24px;
  background: rgba(0,0,0,0.6); color: #fff;
  font-size: 22px; line-height: 1.5; text-align: center;
  border-radius: 6px; z-index: 100;
  opacity: 0; transition: opacity 0.3s;
  pointer-events: none;
}}
#mg-subtitle.visible {{ opacity: 1; }}

#mg-controls {{
  width: 1280px; margin: auto;
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px; background: #111; border-radius: 0 0 8px 8px;
}}
#mg-controls button {{
  background: none; border: none; color: #fff; font-size: 18px; cursor: pointer; padding: 4px 8px;
}}
#mg-progress-wrap {{
  flex: 1; height: 6px; background: #333; border-radius: 3px; cursor: pointer; position: relative;
}}
#mg-progress-bar {{
  height: 100%; width: 0%; background: #5b6ef5; border-radius: 3px; transition: width 0.1s linear;
}}
#mg-time {{ color: #aaa; font-size: 13px; font-variant-numeric: tabular-nums; min-width: 90px; text-align: right; }}

#mg-scene-bar {{
  width: 1280px; margin: auto; display: flex; height: 22px; background: #1a1a1a; gap: 1px;
}}
.scene-dot {{
  height: 100%; display: flex; align-items: center; justify-content: center;
  font-size: 11px; color: #666; cursor: pointer; background: #222; transition: background 0.2s, color 0.2s;
  user-select: none;
}}
.scene-dot:hover {{ background: #333; color: #aaa; }}
.scene-dot.active {{ background: #5b6ef5; color: #fff; }}
</style>
</head>
<body>

<div id="mg-container"></div>
<div id="mg-subtitle"></div>
<div id="mg-controls">
  <button id="btn-play">⏸</button>
  <div id="mg-progress-wrap" onclick="seekTo(event)">
    <div id="mg-progress-bar"></div>
  </div>
  <span id="mg-time">0:00 / 0:00</span>
</div>
<div id="mg-scene-bar"></div>

<script>
window.__MG_IR_DATA__ = {ir_json};
</script>
<script>
{player_js}
</script>
</body>
</html>"""


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _to_web_path(filepath: str) -> str:
    normalized = filepath.replace("\\", "/")
    marker = "/assets/"
    idx = normalized.rfind(marker)
    if idx >= 0:
        return normalized[idx:]
    return filepath


def _normalize_asset_paths(ir_data: dict) -> None:
    if ir_data.get("audio_path"):
        ir_data["audio_path"] = _to_web_path(ir_data["audio_path"])
    for scene in ir_data.get("scenes", []):
        for el in scene.get("elements", []):
            if el.get("src"):
                el["src"] = _to_web_path(el["src"])


def compile_html(ir_data: dict, output_path: str) -> str:
    scenes = ir_data.get("scenes", [])
    issues, should_block = validate_ir(scenes, style=ir_data.get("style"))
    if should_block:
        raise ValueError(f"IR quality check failed: {'; '.join(issues)}")

    _normalize_asset_paths(ir_data)

    style = ir_data.get("style") or {}
    title = _escape_html(ir_data.get("title", "MG动画"))
    font_family = _escape_html(style.get("font_family", "Microsoft YaHei"))
    bg_color = _escape_html(style.get("background_color", "#000000"))
    ir_json = json.dumps(ir_data, ensure_ascii=False)

    html = _HTML_TEMPLATE.format(
        title=title,
        font_family=font_family,
        bg_color=bg_color,
        ir_json=ir_json,
        player_js=_PLAYER_JS,
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    json_path = out.with_suffix(".json")
    json_path.write_text(
        json.dumps(ir_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return str(out)
