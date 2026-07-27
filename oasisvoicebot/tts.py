from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import aiohttp


class TTSProvider(Protocol):
    async def synthesize(self, text: str, voice: str | int) -> bytes: ...

    async def voices(self) -> list[dict]: ...


@dataclass
class VoicevoxProvider:
    base_url: str
    speed: float = 1.15
    timeout_seconds: float = 45.0

    async def synthesize(self, text: str, voice: str | int) -> bytes:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        params = {"text": text, "speaker": int(voice)}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.base_url}/audio_query", params=params) as response:
                response.raise_for_status()
                query = await response.json()
            query["speedScale"] = self.speed
            async with session.post(
                f"{self.base_url}/synthesis",
                params={"speaker": int(voice)},
                json=query,
            ) as response:
                response.raise_for_status()
                return await response.read()

    async def voices(self) -> list[dict]:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.base_url}/speakers") as response:
                response.raise_for_status()
                return await response.json()


@dataclass
class CloneTTSProvider:
    """音声クローンサービス用の小さな共通契約。

    POST /synthesize に {"text": "...", "voice_id": "..."} を送り、
    audio/wav を受け取るサービスを接続する。
    """

    base_url: str
    api_key: str | None = None
    timeout_seconds: float = 90.0

    async def synthesize(self, text: str, voice: str | int) -> bytes:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url.rstrip('/')}/synthesize",
                json={"text": text, "voice_id": str(voice)},
                headers=headers,
            ) as response:
                response.raise_for_status()
                return await response.read()

    async def voices(self) -> list[dict]:
        return []

