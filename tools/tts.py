from __future__ import annotations

import uuid
import struct
from pathlib import Path
from typing import Any

from core.tool import ToolBase, tool


@tool(name="tts_generate")
class TtsGenerate(ToolBase):
    description = "将口播文案转换为语音音频。返回音频文件路径和预估时长。"
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要转换为语音的口播文案文本",
            },
        },
        "required": ["text"],
    }

    async def execute(self, *, text: str, project_dir: str = "", **kwargs: Any) -> dict:
        assets_dir = Path(project_dir) / "artifacts" if project_dir else Path("assets")
        assets_dir.mkdir(parents=True, exist_ok=True)

        audio_id = uuid.uuid4().hex[:8]
        audio_path = assets_dir / f"tts_{audio_id}.mp3"

        chars_per_second = 4
        duration = max(len(text) / chars_per_second, 2.0)

        audio_path.write_bytes(self._silent_mp3(duration))

        return {
            "audio_path": str(audio_path.resolve()),
            "duration_seconds": round(duration, 1),
            "text": text,
            "mock": True,
        }

    @staticmethod
    def _silent_mp3(duration_seconds: float) -> bytes:
        frame_header = bytes([0xFF, 0xFB, 0x90, 0x00])
        frame_data = b"\x00" * 413
        single_frame = frame_header + frame_data
        num_frames = int(duration_seconds / (1152 / 44100)) + 1
        return single_frame * num_frames
