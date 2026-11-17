import pytest

from bot.utils.timeparse import parse_duration_prefix


@pytest.mark.parametrize("text, seconds, reason", [
    ("на час за флуд", 3600, "за флуд"),
    ("на час, причину сам придумай", 3600, "причину сам придумай"),
    ("на полчаса", 1800, ""),
    ("НА ДВА ЧАСА причина", 7200, "причина"),
    ("на одну минуту сам придумай", 60, "сам придумай"),
    ("на сутки", 86400, ""),
    ("на полдня", 43200, ""),
    ("на неделю", 604800, ""),
    ("на 10 минут за флуд", 600, "за флуд"),
    ("1h причина", 3600, "причина"),
    ("за флуд", None, "за флуд"),
    ("на часовню", None, "на часовню"),
])
def test_duration_prefix(text, seconds, reason):
    assert parse_duration_prefix(text) == (seconds, reason)
