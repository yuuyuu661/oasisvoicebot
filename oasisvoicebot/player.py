from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass

import discord

from .tts import TTSProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeechItem:
    text: str
    provider: TTSProvider
    voice: str | int


class GuildPlayer:
    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.queue: asyncio.Queue[SpeechItem] = asyncio.Queue(maxsize=30)
        self.voice_client: discord.VoiceClient | None = None
        self._worker: asyncio.Task | None = None

    def attach(self, voice_client: discord.VoiceClient) -> None:
        self.voice_client = voice_client
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    async def enqueue(self, item: SpeechItem) -> bool:
        if self.queue.full():
            return False
        await self.queue.put(item)
        return True

    def skip(self) -> bool:
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.stop()
            return True
        return False

    async def close(self) -> None:
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect(force=True)
        self.voice_client = None

    async def _run(self) -> None:
        while True:
            item = await self.queue.get()
            path = ""
            try:
                if not self.voice_client or not self.voice_client.is_connected():
                    continue
                audio = await item.provider.synthesize(item.text, item.voice)
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(audio)
                    path = tmp.name
                done = asyncio.Event()
                loop = asyncio.get_running_loop()

                def after(error: Exception | None) -> None:
                    if error:
                        logger.error("音声再生エラー: %s", error)
                    loop.call_soon_threadsafe(done.set)

                source = discord.FFmpegPCMAudio(path, options="-vn")
                self.voice_client.play(source, after=after)
                await done.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("読み上げ処理に失敗しました (guild=%s)", self.guild_id)
            finally:
                self.queue.task_done()
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
