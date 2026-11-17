"""Протокол причин наказаний и границы модерации независимо от характера ИИ."""
import pytest

from bot.services.ai import (build_moderation_messages, explicit_direct_insult,
                             parse_reply, system_prompt)


@pytest.mark.parametrize("punishment, expected", [("варн", ("warn", 0)),
                                                 ("мут 30", ("mute", 30))])
def test_punishment_reason_is_not_reply_text(punishment, expected):
    result = parse_reply(f"РЕАКЦИЯ: 😡\nНАКАЗАНИЕ: {punishment}\n"
                         "ПРИЧИНА: Прямое оскорбление бота.\nПРОПУСК")
    assert result["punish"] == expected
    assert result["reason"] == "Прямое оскорбление бота."
    assert result["skip"] and result["text"] == ""


def test_old_reply_without_reason_still_works():
    result = parse_reply("НАКАЗАНИЕ: варн\nНе оскорбляй.")
    assert result["punish"] == ("warn", 0)
    assert result["reason"] == ""
    assert result["text"] == "Не оскорбляй."


def test_english_reason_and_length_limit():
    result = parse_reply("PUNISH: mute 999\nREASON: " + "x" * 500 + "\nОтвет")
    assert result["punish"] == ("mute", 60)
    assert len(result["reason"]) == 300
    assert result["text"] == "Ответ"


@pytest.mark.parametrize("persona", ["normal", "sassy", "adaptive", "sweet"])
def test_moderation_rules_include_explicit_insults_and_exceptions(persona):
    prompt = system_prompt("esg", persona, "Owner", punish=True)
    assert "ПРИЧИНА:" in prompt
    assert "бот ты тупой" in prompt
    assert "не отменяют прямое оскорбление" in prompt
    assert "цитирование чужого оскорбления" in prompt
    assert "Оценивай только новое сообщение" in prompt


def test_separate_moderator_has_no_conversation_persona():
    messages = build_moderation_messages("esg (@testbot)", "Owner", "User",
                                         "@testbot иди нахуй", [], "")
    assert "независимый модератор" in messages[0]["content"]
    assert "Иди нахуй" in messages[0]["content"]
    assert "@testbot иди нахуй" in messages[1]["content"]
    assert "Зеркаль" not in messages[0]["content"]


@pytest.mark.parametrize("text, reply_target, expected", [
    ("@testbot иди нахуй", False, True),
    ("иди нахуй @testbot", False, True),
    ("@testbot, иди на хуй 😂 шучу", False, True),
    ("иди нахуй", True, True),
    ("иди нахуй", False, False),
    ("esg ты дебил", False, True),
    ("бот, ты тупой", False, True),
    ("бот неправильно отвечает", False, False),
    ('@testbot мне написали «иди нахуй»', False, False),
    ("@testbot не говори иди нахуй", False, False),
    ("@testbot Петя иди нахуй", False, False),
    ("иди нахуй @other", True, False),
    ("@testbot_extra иди нахуй", False, False),
    ("бот не тупой", False, False),
])
def test_explicit_rule_requires_unambiguous_target(text, reply_target, expected):
    assert explicit_direct_insult(text, {"@testbot", "esg", "бот"},
                                  reply_target=reply_target) is expected
