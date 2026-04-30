from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import httpx

from core.tool import ToolBase, tool


@tool(name="image_search")
class ImageSearch(ToolBase):
    description = "在互联网搜索图片并下载到本地。适合搜索真实照片、人物、产品、地标等。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "图片搜索关键词，建议用英文",
            },
        },
        "required": ["query"],
    }

    async def execute(self, *, query: str, project_dir: str = "", **kwargs: Any) -> dict:
        api_key = os.getenv("TAVILY_API_KEY", "")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "query": query,
                    "search_depth": "basic",
                    "include_images": True,
                    "max_results": 3,
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        images = data.get("images", [])
        assets_dir = Path(project_dir) / "artifacts" / "search" if project_dir else Path("assets/search")
        assets_dir.mkdir(parents=True, exist_ok=True)

        for img in images:
            image_url = img.get("url", img) if isinstance(img, dict) else img
            if not image_url:
                continue
            local = await self._download(image_url, assets_dir)
            if local:
                return {"image_path": str(local), "query": query}

        return {"image_path": None, "query": query, "error": "未找到相关图片或下载失败"}

    async def _download(self, url: str, save_dir: Path) -> Path | None:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception:
            return None

        ct = resp.headers.get("content-type", "")
        ext = ".png" if "png" in ct else ".gif" if "gif" in ct else ".webp" if "webp" in ct else ".jpg"
        file_path = save_dir / f"search_{uuid.uuid4().hex[:8]}{ext}"
        file_path.write_bytes(resp.content)
        return file_path.resolve()
