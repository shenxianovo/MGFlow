from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from core.tool import ToolBase, tool

load_dotenv()


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

        api_key = os.getenv("MINIMAX_API_KEY")
        base_url = os.getenv("MINIMAX_API_BASE_URL", "https://api.minimaxi.com/v1")

        if not api_key:
            return await self._mock_execute(text, assets_dir)

        try:
            return await self._real_execute(text, assets_dir, api_key, base_url)
        except Exception as e:
            return await self._mock_execute(text, assets_dir, fallback_reason=str(e))

    async def _real_execute(
        self, text: str, assets_dir: Path, api_key: str, base_url: str
    ) -> dict:
        # PLACEHOLDER_CONTINUE
        audio_id = uuid.uuid4().hex[:8]
        audio_path = assets_dir / f"tts_{audio_id}.mp3"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base_url}/t2a_v2",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "speech-02-hd",
                    "text": text,
                    "stream": False,
                    "voice_setting": {
                        "voice_id": "female-tianmei",
                        "speed": 1.0,
                        "vol": 1.0,
                        "pitch": 0,
                    },
                    "audio_setting": {
                        "sample_rate": 32000,
                        "bitrate": 128000,
                        "format": "mp3",
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("base_resp", {}).get("status_code", 0) != 0:
            error_msg = data.get("base_resp", {}).get("status_msg", "unknown error")
            raise RuntimeError(f"MiniMax TTS error: {error_msg}")

        audio_b64 = data.get("data", {}).get("audio", "")
        if not audio_b64:
            raise RuntimeError("MiniMax TTS returned empty audio")

        audio_bytes = bytes.fromhex(audio_b64)
        audio_path.write_bytes(audio_bytes)

        duration = self._estimate_mp3_duration(audio_bytes)

        return {
            "audio_path": str(audio_path.resolve()),
            "duration_seconds": round(duration, 1),
            "text": text,
            "mock": False,
        }

    async def _mock_execute(
        self, text: str, assets_dir: Path, fallback_reason: str | None = None
    ) -> dict:
        audio_id = uuid.uuid4().hex[:8]
        audio_path = assets_dir / f"tts_{audio_id}.mp3"

        chars_per_second = 4
        duration = max(len(text) / chars_per_second, 2.0)
        audio_path.write_bytes(self._silent_mp3(duration))

        result = {
            "audio_path": str(audio_path.resolve()),
            "duration_seconds": round(duration, 1),
            "text": text,
            "mock": True,
        }
        if fallback_reason:
            result["fallback_reason"] = fallback_reason
        return result

    @staticmethod
    def _estimate_mp3_duration(data: bytes) -> float:
        frame_size = 417
        frame_duration = 1152 / 32000
        num_frames = len(data) // frame_size
        if num_frames > 0:
            return num_frames * frame_duration
        return len(data) / (128000 / 8)

    @staticmethod
    def _silent_mp3(duration_seconds: float) -> bytes:
        frame_header = bytes([0xFF, 0xFB, 0x90, 0x00])
        frame_data = b"\x00" * 413
        single_frame = frame_header + frame_data
        num_frames = int(duration_seconds / (1152 / 44100)) + 1
        return single_frame * num_frames
