from __future__ import annotations

import re

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
WHITESPACE_RE = re.compile(r"\s+")


def normalize_message(content: str, max_length: int) -> str:
    text = URL_RE.sub("URL省略", content)
    text = CUSTOM_EMOJI_RE.sub("", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    if len(text) > max_length:
        text = text[:max_length].rstrip() + "、以下省略"
    return text

