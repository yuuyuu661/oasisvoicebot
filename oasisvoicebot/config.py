from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    discord_token: str
    voicevox_url: str = "http://127.0.0.1:50021"
    default_speaker_id: int = 3
    speech_speed: float = 1.15
    max_text_length: int = 180
    port: int = 8080
    clone_tts_url: str | None = None
    clone_tts_api_key: str | None = None
    clone_voices: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise RuntimeError("DISCORD_TOKEN が設定されていません")

        raw_voices = os.getenv("CLONE_VOICES_JSON", "{}")
        try:
            clone_voices = json.loads(raw_voices)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CLONE_VOICES_JSON はJSONオブジェクトにしてください") from exc
        if not isinstance(clone_voices, dict):
            raise RuntimeError("CLONE_VOICES_JSON はJSONオブジェクトにしてください")

        return cls(
            discord_token=token,
            voicevox_url=os.getenv("VOICEVOX_URL", "http://127.0.0.1:50021").rstrip("/"),
            default_speaker_id=int(os.getenv("DEFAULT_SPEAKER_ID", "3")),
            speech_speed=float(os.getenv("SPEECH_SPEED", "1.15")),
            max_text_length=int(os.getenv("MAX_TEXT_LENGTH", "180")),
            port=int(os.getenv("PORT", "8080")),
            clone_tts_url=os.getenv("CLONE_TTS_URL") or None,
            clone_tts_api_key=os.getenv("CLONE_TTS_API_KEY") or None,
            clone_voices={str(k): str(v) for k, v in clone_voices.items()},
        )

