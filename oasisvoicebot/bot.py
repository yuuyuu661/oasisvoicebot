from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from .config import Settings
from .health import start_health_server
from .player import GuildPlayer, SpeechItem
from .text import normalize_message
from .tts import CloneTTSProvider, TTSProvider, VoicevoxProvider

logger = logging.getLogger(__name__)


@dataclass
class GuildState:
    text_channel_id: int | None = None
    provider_name: str = "voicevox"
    voice: str | int = 3


class OasisVoiceBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.states: dict[int, GuildState] = {}
        self.players: dict[int, GuildPlayer] = {}
        self.providers: dict[str, TTSProvider] = {
            "voicevox": VoicevoxProvider(settings.voicevox_url, settings.speech_speed)
        }
        if settings.clone_tts_url:
            self.providers["clone"] = CloneTTSProvider(
                settings.clone_tts_url, settings.clone_tts_api_key
            )

    async def setup_hook(self) -> None:
        await self.tree.sync()
        self.health_server = await start_health_server(self.settings.port)
        logger.info("health server started on port %s", self.settings.port)

    async def close(self) -> None:
        for player in self.players.values():
            await player.close()
        if hasattr(self, "health_server"):
            self.health_server.close()
            await self.health_server.wait_closed()
        await super().close()

    def state_for(self, guild_id: int) -> GuildState:
        return self.states.setdefault(
            guild_id, GuildState(voice=self.settings.default_speaker_id)
        )

    def player_for(self, guild_id: int) -> GuildPlayer:
        return self.players.setdefault(guild_id, GuildPlayer(guild_id))


def build_bot(settings: Settings) -> OasisVoiceBot:
    bot = OasisVoiceBot(settings)

    @bot.event
    async def on_ready() -> None:
        logger.info("logged in as %s (%s)", bot.user, bot.user.id if bot.user else "?")

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        state = bot.state_for(message.guild.id)
        if state.text_channel_id != message.channel.id:
            return
        text = normalize_message(message.content, settings.max_text_length)
        if not text and message.attachments:
            text = "ファイルが添付されました"
        if not text:
            return
        provider = bot.providers.get(state.provider_name)
        if provider:
            await bot.player_for(message.guild.id).enqueue(
                SpeechItem(text, provider, state.voice)
            )

    @bot.tree.command(name="join", description="現在のボイスチャンネルに参加して読み上げを開始")
    async def join(interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で使用してください。", ephemeral=True)
            return
        channel = interaction.user.voice.channel if interaction.user.voice else None
        if not channel:
            await interaction.response.send_message(
                "先にボイスチャンネルへ参加してください。", ephemeral=True
            )
            return
        voice_client = interaction.guild.voice_client
        if voice_client:
            await voice_client.move_to(channel)
        else:
            voice_client = await channel.connect()
        bot.player_for(interaction.guild.id).attach(voice_client)
        bot.state_for(interaction.guild.id).text_channel_id = interaction.channel_id
        await interaction.response.send_message(
            f"{channel.mention} に参加しました。このチャンネルを読み上げます。"
        )

    @bot.tree.command(name="leave", description="読み上げを終了して退出")
    async def leave(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        await bot.player_for(interaction.guild.id).close()
        bot.players.pop(interaction.guild.id, None)
        bot.states.pop(interaction.guild.id, None)
        await interaction.response.send_message("読み上げを終了しました。")

    @bot.tree.command(name="skip", description="再生中の読み上げをスキップ")
    async def skip(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        skipped = bot.player_for(interaction.guild.id).skip()
        await interaction.response.send_message(
            "スキップしました。" if skipped else "再生中の音声はありません。",
            ephemeral=True,
        )

    @bot.tree.command(name="voice", description="読み上げ音声を変更")
    @app_commands.describe(provider="voicevox または clone", voice="VOICEVOXのスタイルID、またはクローン音声名")
    async def voice(interaction: discord.Interaction, provider: str, voice: str) -> None:
        if not interaction.guild:
            return
        provider = provider.lower()
        if provider not in bot.providers:
            await interaction.response.send_message(
                f"利用できるプロバイダー: {', '.join(bot.providers)}", ephemeral=True
            )
            return
        state = bot.state_for(interaction.guild.id)
        if provider == "voicevox":
            try:
                selected: str | int = int(voice)
            except ValueError:
                await interaction.response.send_message(
                    "VOICEVOXではスタイルIDを数字で指定してください。", ephemeral=True
                )
                return
        else:
            selected = settings.clone_voices.get(voice, voice)
        state.provider_name = provider
        state.voice = selected
        await interaction.response.send_message(
            f"読み上げ音声を `{provider}:{voice}` に変更しました。"
        )

    @bot.tree.command(name="voices", description="VOICEVOXで利用できる音声を表示")
    async def voices(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            speakers = await bot.providers["voicevox"].voices()
            lines = [
                f"{speaker['name']}: "
                + ", ".join(f"{style['name']}={style['id']}" for style in speaker["styles"])
                for speaker in speakers
            ]
            text = "\n".join(lines)
            await interaction.followup.send(text[:1900], ephemeral=True)
        except Exception as exc:
            logger.exception("VOICEVOX音声一覧の取得に失敗")
            await interaction.followup.send(
                f"VOICEVOX Engineへ接続できません: {type(exc).__name__}", ephemeral=True
            )

    return bot


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    bot = build_bot(settings)
    asyncio.run(bot.start(settings.discord_token))

