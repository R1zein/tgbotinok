import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: str
    api_id: int | None
    api_hash: str | None
    userbot_session: str | None
    userbot_min_delay: float
    gift_poll_interval: int
    username_check_delay: float
    username_cycle_interval: int
    max_usernames: int
    business_cache_days: int
    proxy: str | None = None

    @property
    def userbot_configured(self) -> bool:
        return bool(self.api_id and self.api_hash and self.userbot_session)


def load_config() -> Config:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN не задан (см. .env.example)")
    admins = frozenset(
        int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
    )
    api_id = os.getenv("API_ID", "").strip()
    return Config(
        bot_token=token,
        admin_ids=admins,
        db_path=os.getenv("DB_PATH", "data/bot.db"),
        api_id=int(api_id) if api_id else None,
        api_hash=os.getenv("API_HASH", "").strip() or None,
        userbot_session=os.getenv("USERBOT_SESSION", "").strip() or None,
        userbot_min_delay=_float("USERBOT_MIN_DELAY", 1.0),
        gift_poll_interval=_int("GIFT_POLL_INTERVAL", 30),
        username_check_delay=_float("USERNAME_CHECK_DELAY", 2.0),
        username_cycle_interval=_int("USERNAME_CYCLE_INTERVAL", 60),
        max_usernames=_int("MAX_USERNAMES", 10),
        business_cache_days=_int("BUSINESS_CACHE_DAYS", 14),
        proxy=os.getenv("PROXY", "").strip() or None,
    )
