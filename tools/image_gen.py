from __future__ import annotations

import os
import re
import uuid
import base64
import asyncio
from pathlib import Path
from typing import Any

import openai

from core.tool import ToolBase, tool


@tool(name="image_generate")
class ImageGenerate(ToolBase):
    description = "根据文本描述生成图片。适合生成插画、图标、抽象概念图、创意画面等。"
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "图片描述prompt，建议用英文",
            },
            "size": {
                "type": "string",
                "enum": ["1024x1024", "1536x1024", "1024x1536"],
                "description": "图片尺寸，默认1536x1024（横版）",
            },
        },
        "required": ["prompt"],
    }

    async def execute(self, *, prompt: str, size: str = "1536x1024", project_dir: str = "", **kwargs: Any) -> dict:
        client = openai.AsyncOpenAI(
            base_url=os.getenv("LLM_GATEWAY_BASE_URL"),
            api_key=os.getenv("LLM_GATEWAY_API_KEY"),
        )

        max_retries = 3
        resp = None
        for attempt in range(max_retries):
            try:
                resp = await client.images.generate(
                    model="gpt-image-2", prompt=prompt, n=1, size=size,
                )
                break
            except openai.RateLimitError as e:
                wait = 20
                match = re.search(r"retry after (\d+)", str(e), re.IGNORECASE)
                if match:
                    wait = int(match.group(1)) + 2
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait)
                    continue
                return {"error": "图片生成限流，建议改用 image_search", "prompt": prompt}
            except Exception as e:
                return {"error": f"图片生成失败: {e}", "prompt": prompt}

        assets_dir = Path(project_dir) / "artifacts" if project_dir else Path("assets")
        assets_dir.mkdir(parents=True, exist_ok=True)
        image_id = uuid.uuid4().hex[:8]
        image_path = assets_dir / f"gen_{image_id}.png"

        image_data = resp.data[0]
        if hasattr(image_data, "b64_json") and image_data.b64_json:
            image_path.write_bytes(base64.b64decode(image_data.b64_json))
        elif hasattr(image_data, "url") and image_data.url:
            import httpx
            r = await httpx.AsyncClient().get(image_data.url, timeout=30)
            image_path.write_bytes(r.content)
        else:
            return {"error": "图片生成返回格式异常", "prompt": prompt}

        return {"image_path": str(image_path.resolve()), "prompt": prompt, "size": size}
