from __future__ import annotations

import asyncio
import contextlib
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
        for player in list(self.players.values()):
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

    async def stop_guild(self, guild_id: int) -> None:
        player = self.players.pop(guild_id, None)
        self.states.pop(guild_id, None)
        if player:
            await player.close()


VoiceChannel = discord.VoiceChannel | discord.StageChannel


class JoinConfirmationView(discord.ui.View):
    def __init__(
        self,
        bot: OasisVoiceBot,
        requester_id: int,
        guild_id: int,
        target_channel: VoiceChannel,
        text_channel_id: int,
    ) -> None:
        super().__init__(timeout=60)
        self.bot = bot
        self.requester_id = requester_id
        self.guild_id = guild_id
        self.target_channel = target_channel
        self.text_channel_id = text_channel_id
        self.message: discord.InteractionMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "この確認を操作できるのは `/join` を実行したユーザーだけです。",
            ephemeral=True,
        )
        return False

    def disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(label="接続", style=discord.ButtonStyle.primary)
    async def connect(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild or guild.id != self.guild_id:
            await interaction.followup.send("接続先のサーバーを確認できません。", ephemeral=True)
            return

        self.disable_buttons()
        if interaction.message:
            await interaction.message.edit(view=self)

        try:
            await self.bot.stop_guild(guild.id)
            voice_client = await self.target_channel.connect()
        except Exception as exc:
            logger.exception("確認後のボイスチャンネル接続に失敗")
            await interaction.followup.send(
                f"ボイスチャンネルへ接続できませんでした: {type(exc).__name__}",
                ephemeral=True,
            )
            self.stop()
            return

        self.bot.player_for(guild.id).attach(voice_client)
        self.bot.state_for(guild.id).text_channel_id = self.text_channel_id
        await interaction.followup.send(
            f"{self.target_channel.mention} に接続先を変更しました。",
            ephemeral=True,
        )
        self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.disable_buttons()
        await interaction.response.edit_message(
            content="接続先の変更をキャンセルしました。",
            view=self,
        )
        self.stop()

    async def on_timeout(self) -> None:
        self.disable_buttons()
        if self.message:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(content="接続確認がタイムアウトしました。", view=self)


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

    @bot.event
    async def on_voice_state_update(
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if not bot.user or member.id != bot.user.id:
            return
        if before.channel is not None and after.channel is None:
            logger.info("external voice disconnect detected (guild=%s)", member.guild.id)
            await bot.stop_guild(member.guild.id)

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
        if voice_client and voice_client.channel == channel:
            bot.player_for(interaction.guild.id).attach(voice_client)
            bot.state_for(interaction.guild.id).text_channel_id = interaction.channel_id
            await interaction.response.send_message(
                f"すでに {channel.mention} に接続しています。"
                "このチャンネルを読み上げ対象に設定しました。",
                ephemeral=True,
            )
            return

        if voice_client and voice_client.channel:
            current_channel = voice_client.channel
            can_view = current_channel.permissions_for(interaction.user).view_channel
            current_name = current_channel.mention if can_view else "アクセスなし"
            view = JoinConfirmationView(
                bot=bot,
                requester_id=interaction.user.id,
                guild_id=interaction.guild.id,
                target_channel=channel,
                text_channel_id=interaction.channel_id,
            )
            await interaction.response.send_message(
                "Botはすでに別のボイスチャンネルで使用中です。\n"
                f"現在の接続先: {current_name}\n"
                f"接続人数: **{len(current_channel.members)}人**\n"
                f"新しい接続先: {channel.mention}\n\n"
                "現在の読み上げを終了して接続先を変更しますか？",
                view=view,
                ephemeral=True,
            )
            view.message = await interaction.original_response()
            return

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
        await bot.stop_guild(interaction.guild.id)
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

    provider_choices = [
        app_commands.Choice(
            name="VOICEVOX",
            value="voicevox",
        )
    ]
    if "clone" in bot.providers:
        provider_choices.append(
            app_commands.Choice(
                name="録音サンプル音声",
                value="clone",
            )
        )

    async def voice_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        provider = getattr(interaction.namespace, "provider", None)
        current_lower = current.lower()

        if provider == "voicevox":
            try:
                speakers = await bot.providers["voicevox"].voices()
            except Exception:
                logger.exception("VOICEVOX音声候補の取得に失敗")
                return []

            choices: list[app_commands.Choice[str]] = []
            for speaker in speakers:
                for style in speaker.get("styles", []):
                    label = f"{speaker['name']} / {style['name']}"
                    if current_lower and current_lower not in label.lower():
                        continue
                    choices.append(
                        app_commands.Choice(
                            name=label[:100],
                            value=str(style["id"]),
                        )
                    )
                    if len(choices) == 25:
                        return choices
            return choices

        if provider == "clone":
            return [
                app_commands.Choice(name=name[:100], value=name)
                for name in settings.clone_voices
                if not current_lower or current_lower in name.lower()
            ][:25]

        return []

    @bot.tree.command(name="voice", description="読み上げ音声を変更")
    @app_commands.describe(
        provider="音声エンジンを選択",
        voice="選択したエンジンの音声を選択",
    )
    @app_commands.choices(provider=provider_choices)
    @app_commands.autocomplete(voice=voice_autocomplete)
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
