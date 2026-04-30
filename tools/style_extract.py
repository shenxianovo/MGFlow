from __future__ import annotations

import os
import json
import base64
from pathlib import Path
from typing import Any

import openai

from core.tool import ToolBase, tool

_STYLE_PROMPT = """你是一个视觉风格分析专家。分析用户提供的 MG 动画参考图片，提取视觉风格特征。

请输出一个 JSON 对象，包含以下字段：
{
  "palette": ["#hex1", "#hex2", ...],
  "font_family": "字体风格描述",
  "background_color": "#hex",
  "mood": "整体氛围关键词",
  "layout_style": "布局风格描述",
  "graphic_style": "图形风格",
  "animation_hints": "建议的动画风格"
}

只输出 JSON，不要其他文字。"""


@tool(name="style_extract")
class StyleExtract(ToolBase):
    description = "分析参考图片的视觉风格，提取配色方案、字体风格、布局偏好等，返回结构化 style 对象。"
    parameters = {
        "type": "object",
        "properties": {
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "参考图片的本地绝对路径列表（1-3张）",
            },
        },
        "required": ["image_paths"],
    }

    async def execute(self, *, image_paths: list[str], **kwargs: Any) -> dict:
        if not image_paths:
            return {"error": "请提供至少一张参考图片路径"}

        content: list[dict] = [{"type": "text", "text": "请分析以下参考图片的视觉风格："}]
        valid_count = 0

        for img_path in image_paths[:3]:
            p = Path(img_path)
            if not p.exists():
                continue
            raw = p.read_bytes()
            b64 = base64.b64encode(raw).decode()
            mime = self._detect_mime(raw)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
            valid_count += 1

        if valid_count == 0:
            return {"error": "没有找到有效的图片文件"}

        client = openai.AsyncOpenAI(
            base_url=os.getenv("LLM_GATEWAY_BASE_URL"),
            api_key=os.getenv("LLM_GATEWAY_API_KEY"),
        )

        try:
            resp = await client.chat.completions.create(
                model="claude-4.6-sonnet",
                messages=[
                    {"role": "system", "content": _STYLE_PROMPT},
                    {"role": "user", "content": content},
                ],
                max_tokens=1000,
                temperature=0.2,
            )
        except Exception as e:
            return {"error": f"风格分析失败: {e}"}

        raw_text = (resp.choices[0].message.content or "").strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            style = json.loads(raw_text)
        except json.JSONDecodeError:
            return {"error": "风格分析结果解析失败", "raw": raw_text}

        return {"style": style, "analyzed_images": valid_count}

    @staticmethod
    def _detect_mime(raw: bytes) -> str:
        if raw[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if raw[:4] == b"GIF8":
            return "image/gif"
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            return "image/webp"
        return "image/jpeg"
