from oasisvoicebot.text import normalize_message


def test_normalize_message_replaces_urls_and_whitespace() -> None:
    assert normalize_message("見て https://example.com/a\n  です", 100) == "見て URL省略 です"


def test_normalize_message_truncates() -> None:
    assert normalize_message("あ" * 10, 5) == "あ" * 5 + "、以下省略"


def test_normalize_message_removes_custom_emoji() -> None:
    assert normalize_message("こんにちは <:wave:123456>", 100) == "こんにちは"

