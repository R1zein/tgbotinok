import json
import os
import time
from typing import Any, Iterable

import aiosqlite

from .features import GLOBAL_FEATURES, GROUP_DEFAULTS, USER_DEFAULTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    settings TEXT NOT NULL DEFAULT '{}',
    is_banned INTEGER NOT NULL DEFAULT 0,
    started INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER
);
CREATE TABLE IF NOT EXISTS chats(
    id INTEGER PRIMARY KEY,
    title TEXT,
    type TEXT,
    settings TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER
);
CREATE TABLE IF NOT EXISTS global_settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS chat_members(
    chat_id INTEGER, user_id INTEGER, first_name TEXT, username TEXT, last_seen INTEGER,
    PRIMARY KEY(chat_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_cm_username ON chat_members(chat_id, username);
CREATE TABLE IF NOT EXISTS warns(
    chat_id INTEGER, user_id INTEGER, count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS economy(
    chat_id INTEGER, user_id INTEGER,
    balance INTEGER NOT NULL DEFAULT 0, last_bonus INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS marriages(
    chat_id INTEGER, user1 INTEGER, user2 INTEGER, since INTEGER,
    PRIMARY KEY(chat_id, user1, user2)
);
CREATE TABLE IF NOT EXISTS business_connections(
    id TEXT PRIMARY KEY, user_id INTEGER, user_chat_id INTEGER,
    enabled INTEGER, can_reply INTEGER, can_delete INTEGER, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS business_messages(
    bc_id TEXT, chat_id INTEGER, message_id INTEGER,
    from_id INTEGER, from_name TEXT, from_username TEXT,
    text TEXT, media_type TEXT, file_id TEXT, date INTEGER,
    PRIMARY KEY(bc_id, chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_bm_date ON business_messages(date);
CREATE TABLE IF NOT EXISTS business_chat_modes(
    owner_id INTEGER, chat_id INTEGER,
    style TEXT NOT NULL DEFAULT 'none',
    clone INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(owner_id, chat_id)
);
CREATE TABLE IF NOT EXISTS business_mutes(
    owner_id INTEGER, chat_id INTEGER, bc_id TEXT, since INTEGER,
    PRIMARY KEY(owner_id, chat_id)
);
CREATE TABLE IF NOT EXISTS games(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT, chat_id INTEGER, bc_id TEXT, message_id INTEGER,
    p1 INTEGER, p2 INTEGER, p1_name TEXT, p2_name TEXT,
    state TEXT, status TEXT, created_at INTEGER, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS gift_collections(
    slug TEXT PRIMARY KEY, title TEXT, gift_id INTEGER,
    last_num INTEGER, status TEXT NOT NULL DEFAULT 'new', updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS gift_subs(
    user_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    all_collections INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gift_sub_collections(
    user_id INTEGER, slug TEXT, PRIMARY KEY(user_id, slug)
);
CREATE TABLE IF NOT EXISTS gift_attrs(
    slug TEXT, kind TEXT, name TEXT, rarity TEXT, sort_key INTEGER,
    PRIMARY KEY(slug, kind, name)
);
CREATE TABLE IF NOT EXISTS resale_subs(
    user_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'any',
    discount INTEGER NOT NULL DEFAULT 20,
    all_collections INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS resale_sub_collections(
    user_id INTEGER, slug TEXT, PRIMARY KEY(user_id, slug)
);
CREATE TABLE IF NOT EXISTS gift_sub_attrs(
    user_id INTEGER, kind TEXT, slug TEXT, name TEXT,
    PRIMARY KEY(user_id, kind, slug, name)
);
CREATE TABLE IF NOT EXISTS username_watch(
    user_id INTEGER, username TEXT, status TEXT, created_at INTEGER,
    PRIMARY KEY(user_id, username)
);
"""


def now() -> int:
    return int(time.time())


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self._global_cache: dict[str, str] = {}

    # ---------- базовое ----------
    async def connect(self) -> None:
        if os.path.dirname(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self._migrate()
        await self.conn.commit()
        async with self.conn.execute("SELECT key, value FROM global_settings") as cur:
            self._global_cache = {r["key"]: r["value"] for r in await cur.fetchall()}

    async def _migrate(self) -> None:
        """Добавляет столбцы, появившиеся в новых версиях, в уже существующую базу."""
        async with self.conn.execute("PRAGMA table_info(business_messages)") as cur:
            cols = {r["name"] for r in await cur.fetchall()}
        if "auto" not in cols:
            # 1 — сообщение отправил чужой бизнес-бот (его правки/удаление не показываем)
            await self.conn.execute(
                "ALTER TABLE business_messages ADD COLUMN auto INTEGER NOT NULL DEFAULT 0"
            )

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = await self.conn.execute(sql, tuple(params))
        await self.conn.commit()
        return cur.lastrowid

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return list(await cur.fetchall())

    async def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = await self.fetchone(sql, params)
        return row[0] if row else None

    # ---------- глобальные функции ----------
    def feature_enabled(self, key: str) -> bool:
        return self._global_cache.get(f"feature:{key}", "1") == "1"

    async def set_feature(self, key: str, enabled: bool) -> None:
        assert key in GLOBAL_FEATURES
        k = f"feature:{key}"
        self._global_cache[k] = "1" if enabled else "0"
        await self.execute(
            "INSERT INTO global_settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, self._global_cache[k]),
        )

    # ---------- пользователи ----------
    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None,
                          started: bool = False) -> None:
        await self.execute(
            "INSERT INTO users(id, username, first_name, started, created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
            "first_name=excluded.first_name, started=MAX(users.started, excluded.started)",
            (user_id, username, first_name, int(started), now()),
        )

    async def is_banned(self, user_id: int) -> bool:
        return bool(await self.scalar("SELECT is_banned FROM users WHERE id=?", (user_id,)))

    async def set_banned(self, user_id: int, banned: bool) -> None:
        await self.execute(
            "INSERT INTO users(id, is_banned, created_at) VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET is_banned=excluded.is_banned",
            (user_id, int(banned), now()),
        )

    async def get_user_settings(self, user_id: int) -> dict:
        raw = await self.scalar("SELECT settings FROM users WHERE id=?", (user_id,))
        data = dict(USER_DEFAULTS)
        if raw:
            data.update(json.loads(raw))
        return data

    async def set_user_setting(self, user_id: int, key: str, value: Any) -> dict:
        settings = await self.get_user_settings(user_id)
        settings[key] = value
        await self.execute(
            "INSERT INTO users(id, settings, created_at) VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET settings=excluded.settings",
            (user_id, json.dumps(settings), now()),
        )
        return settings

    # ---------- чаты ----------
    async def upsert_chat(self, chat_id: int, title: str | None, chat_type: str,
                          active: bool = True) -> None:
        await self.execute(
            "INSERT INTO chats(id, title, type, active, created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, type=excluded.type, "
            "active=excluded.active",
            (chat_id, title, chat_type, int(active), now()),
        )

    async def get_chat_settings(self, chat_id: int) -> dict:
        raw = await self.scalar("SELECT settings FROM chats WHERE id=?", (chat_id,))
        data = dict(GROUP_DEFAULTS)
        if raw:
            data.update(json.loads(raw))
        return data

    async def set_chat_setting(self, chat_id: int, key: str, value: Any) -> dict:
        settings = await self.get_chat_settings(chat_id)
        settings[key] = value
        await self.execute("UPDATE chats SET settings=? WHERE id=?", (json.dumps(settings), chat_id))
        return settings

    async def group_feature(self, chat_id: int, key: str) -> bool:
        if not self.feature_enabled(key):
            return False
        return bool((await self.get_chat_settings(chat_id)).get(key, True))

    # ---------- участники ----------
    async def touch_member(self, chat_id: int, user_id: int, first_name: str | None,
                           username: str | None) -> None:
        await self.execute(
            "INSERT INTO chat_members(chat_id, user_id, first_name, username, last_seen) "
            "VALUES(?,?,?,?,?) ON CONFLICT(chat_id, user_id) DO UPDATE SET "
            "first_name=excluded.first_name, username=excluded.username, last_seen=excluded.last_seen",
            (chat_id, user_id, first_name, username.lower() if username else None, now()),
        )

    async def remove_member(self, chat_id: int, user_id: int) -> None:
        await self.execute("DELETE FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, user_id))

    async def find_member_by_username(self, chat_id: int, username: str) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM chat_members WHERE chat_id=? AND username=?",
            (chat_id, username.lstrip("@").lower()),
        )

    async def get_member(self, chat_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM chat_members WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )

    async def list_members(self, chat_id: int) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM chat_members WHERE chat_id=? ORDER BY last_seen DESC", (chat_id,)
        )

    # ---------- варны ----------
    async def add_warn(self, chat_id: int, user_id: int, delta: int = 1) -> int:
        await self.execute(
            "INSERT INTO warns(chat_id, user_id, count) VALUES(?,?,MAX(?,0)) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET count=MAX(warns.count + ?, 0)",
            (chat_id, user_id, delta, delta),
        )
        return await self.get_warns(chat_id, user_id)

    async def get_warns(self, chat_id: int, user_id: int) -> int:
        return await self.scalar(
            "SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        ) or 0

    async def reset_warns(self, chat_id: int, user_id: int) -> None:
        await self.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))

    # ---------- экономика ----------
    START_BALANCE = 1000

    async def get_balance(self, chat_id: int, user_id: int) -> int:
        row = await self.fetchone(
            "SELECT balance FROM economy WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        if row is None:
            await self.execute(
                "INSERT OR IGNORE INTO economy(chat_id, user_id, balance) VALUES(?,?,?)",
                (chat_id, user_id, self.START_BALANCE),
            )
            return self.START_BALANCE
        return row["balance"]

    async def add_balance(self, chat_id: int, user_id: int, delta: int) -> int:
        await self.get_balance(chat_id, user_id)
        await self.execute(
            "UPDATE economy SET balance=balance+? WHERE chat_id=? AND user_id=?",
            (delta, chat_id, user_id),
        )
        return await self.get_balance(chat_id, user_id)

    async def try_spend(self, chat_id: int, user_id: int, amount: int) -> bool:
        """Атомарно списывает сумму, если хватает средств."""
        await self.get_balance(chat_id, user_id)
        cur = await self.conn.execute(
            "UPDATE economy SET balance=balance-? WHERE chat_id=? AND user_id=? AND balance>=?",
            (amount, chat_id, user_id, amount),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_last_bonus(self, chat_id: int, user_id: int) -> int:
        await self.get_balance(chat_id, user_id)
        return await self.scalar(
            "SELECT last_bonus FROM economy WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        ) or 0

    async def set_last_bonus(self, chat_id: int, user_id: int, ts: int) -> None:
        await self.execute(
            "UPDATE economy SET last_bonus=? WHERE chat_id=? AND user_id=?", (ts, chat_id, user_id)
        )

    async def top_balances(self, chat_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT e.user_id, e.balance, m.first_name FROM economy e "
            "LEFT JOIN chat_members m ON m.chat_id=e.chat_id AND m.user_id=e.user_id "
            "WHERE e.chat_id=? ORDER BY e.balance DESC LIMIT ?",
            (chat_id, limit),
        )

    # ---------- браки ----------
    async def get_marriage(self, chat_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM marriages WHERE chat_id=? AND (user1=? OR user2=?)",
            (chat_id, user_id, user_id),
        )

    async def add_marriage(self, chat_id: int, u1: int, u2: int) -> None:
        await self.execute(
            "INSERT INTO marriages(chat_id, user1, user2, since) VALUES(?,?,?,?)",
            (chat_id, u1, u2, now()),
        )

    async def remove_marriage(self, chat_id: int, user_id: int) -> None:
        await self.execute(
            "DELETE FROM marriages WHERE chat_id=? AND (user1=? OR user2=?)",
            (chat_id, user_id, user_id),
        )

    async def list_marriages(self, chat_id: int) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM marriages WHERE chat_id=? ORDER BY since", (chat_id,)
        )

    # ---------- бизнес ----------
    async def save_business_connection(self, bc_id: str, user_id: int, user_chat_id: int,
                                       enabled: bool, can_reply: bool, can_delete: bool) -> None:
        await self.execute(
            "INSERT INTO business_connections(id, user_id, user_chat_id, enabled, can_reply, "
            "can_delete, updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "user_id=excluded.user_id, user_chat_id=excluded.user_chat_id, enabled=excluded.enabled, "
            "can_reply=excluded.can_reply, can_delete=excluded.can_delete, updated_at=excluded.updated_at",
            (bc_id, user_id, user_chat_id, int(enabled), int(can_reply), int(can_delete), now()),
        )

    async def get_business_connection(self, bc_id: str) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM business_connections WHERE id=?", (bc_id,))

    async def get_user_business_connection(self, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM business_connections WHERE user_id=? ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        )

    async def cache_business_message(self, bc_id: str, chat_id: int, message_id: int,
                                     from_id: int, from_name: str, from_username: str | None,
                                     text: str | None, media_type: str | None,
                                     file_id: str | None, date: int, auto: bool = False) -> None:
        await self.execute(
            "INSERT INTO business_messages(bc_id, chat_id, message_id, from_id, from_name, "
            "from_username, text, media_type, file_id, date, auto) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(bc_id, chat_id, message_id) DO UPDATE SET text=excluded.text, "
            "media_type=excluded.media_type, file_id=excluded.file_id, "
            "auto=MAX(business_messages.auto, excluded.auto)",
            (bc_id, chat_id, message_id, from_id, from_name, from_username, text,
             media_type, file_id, date, int(auto)),
        )

    async def get_business_message(self, bc_id: str, chat_id: int, message_id: int):
        return await self.fetchone(
            "SELECT * FROM business_messages WHERE bc_id=? AND chat_id=? AND message_id=?",
            (bc_id, chat_id, message_id),
        )

    async def delete_business_message(self, bc_id: str, chat_id: int, message_id: int) -> None:
        await self.execute(
            "DELETE FROM business_messages WHERE bc_id=? AND chat_id=? AND message_id=?",
            (bc_id, chat_id, message_id),
        )

    async def cleanup_business_messages(self, older_than_days: int) -> None:
        await self.execute(
            "DELETE FROM business_messages WHERE date < ?", (now() - older_than_days * 86400,)
        )

    async def get_chat_mode(self, owner_id: int, chat_id: int) -> dict:
        row = await self.fetchone(
            "SELECT style, clone FROM business_chat_modes WHERE owner_id=? AND chat_id=?",
            (owner_id, chat_id),
        )
        return dict(row) if row else {"style": "none", "clone": 0}

    async def set_chat_mode(self, owner_id: int, chat_id: int, **fields: Any) -> dict:
        mode = await self.get_chat_mode(owner_id, chat_id)
        mode.update(fields)
        await self.execute(
            "INSERT INTO business_chat_modes(owner_id, chat_id, style, clone) VALUES(?,?,?,?) "
            "ON CONFLICT(owner_id, chat_id) DO UPDATE SET style=excluded.style, clone=excluded.clone",
            (owner_id, chat_id, mode["style"], int(mode["clone"])),
        )
        return mode

    async def is_business_muted(self, owner_id: int, chat_id: int) -> bool:
        return await self.fetchone(
            "SELECT 1 FROM business_mutes WHERE owner_id=? AND chat_id=?", (owner_id, chat_id)
        ) is not None

    async def set_business_mute(self, owner_id: int, chat_id: int, bc_id: str, muted: bool) -> None:
        if muted:
            await self.execute(
                "INSERT OR REPLACE INTO business_mutes(owner_id, chat_id, bc_id, since) VALUES(?,?,?,?)",
                (owner_id, chat_id, bc_id, now()),
            )
        else:
            await self.execute(
                "DELETE FROM business_mutes WHERE owner_id=? AND chat_id=?", (owner_id, chat_id)
            )

    # ---------- игры ----------
    async def create_game(self, kind: str, chat_id: int, bc_id: str | None, p1: int, p1_name: str,
                          p2: int | None, p2_name: str | None, state: dict, status: str) -> int:
        return await self.execute(
            "INSERT INTO games(kind, chat_id, bc_id, p1, p2, p1_name, p2_name, state, status, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (kind, chat_id, bc_id, p1, p2, p1_name, p2_name, json.dumps(state), status, now(), now()),
        )

    async def get_game(self, game_id: int) -> dict | None:
        row = await self.fetchone("SELECT * FROM games WHERE id=?", (game_id,))
        if not row:
            return None
        game = dict(row)
        game["state"] = json.loads(game["state"] or "{}")
        return game

    async def update_game(self, game_id: int, **fields: Any) -> None:
        if "state" in fields:
            fields["state"] = json.dumps(fields["state"])
        fields["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in fields)
        await self.execute(f"UPDATE games SET {cols} WHERE id=?", (*fields.values(), game_id))

    # ---------- подарки ----------
    async def get_gift_sub(self, user_id: int) -> dict:
        row = await self.fetchone("SELECT * FROM gift_subs WHERE user_id=?", (user_id,))
        if not row:
            return {"user_id": user_id, "enabled": 0, "all_collections": 1}
        return dict(row)

    async def set_gift_sub(self, user_id: int, **fields: Any) -> None:
        sub = await self.get_gift_sub(user_id)
        sub.update(fields)
        await self.execute(
            "INSERT INTO gift_subs(user_id, enabled, all_collections) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, "
            "all_collections=excluded.all_collections",
            (user_id, int(sub["enabled"]), int(sub["all_collections"])),
        )

    async def get_user_gift_collections(self, user_id: int) -> set[str]:
        rows = await self.fetchall("SELECT slug FROM gift_sub_collections WHERE user_id=?", (user_id,))
        return {r["slug"] for r in rows}

    async def toggle_user_gift_collection(self, user_id: int, slug: str) -> bool:
        if slug in await self.get_user_gift_collections(user_id):
            await self.execute(
                "DELETE FROM gift_sub_collections WHERE user_id=? AND slug=?", (user_id, slug)
            )
            return False
        await self.execute(
            "INSERT OR IGNORE INTO gift_sub_collections(user_id, slug) VALUES(?,?)", (user_id, slug)
        )
        return True

    async def clear_user_gift_collections(self, user_id: int) -> None:
        await self.execute("DELETE FROM gift_sub_collections WHERE user_id=?", (user_id,))

    async def list_gift_collections(self, only_ok: bool = True) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM gift_collections"
        if only_ok:
            sql += " WHERE status IN ('ok', 'new')"
        return await self.fetchall(sql + " ORDER BY title")

    async def gift_subscribers_for(self, slug: str, backdrop: str | None = None,
                                   pattern: str | None = None) -> list[int]:
        """Подписчики коллекции с учётом фильтров по фону (общий) и узору (для коллекции)."""
        rows = await self.fetchall(
            "SELECT s.user_id FROM gift_subs s WHERE s.enabled=1 AND (s.all_collections=1 OR "
            "s.user_id IN (SELECT user_id FROM gift_sub_collections WHERE slug=?)) "
            "AND (NOT EXISTS (SELECT 1 FROM gift_sub_attrs a WHERE a.user_id=s.user_id "
            "AND a.kind='backdrop') OR EXISTS (SELECT 1 FROM gift_sub_attrs a "
            "WHERE a.user_id=s.user_id AND a.kind='backdrop' AND a.name=?)) "
            "AND (NOT EXISTS (SELECT 1 FROM gift_sub_attrs a WHERE a.user_id=s.user_id "
            "AND a.kind='pattern' AND a.slug=?) OR EXISTS (SELECT 1 FROM gift_sub_attrs a "
            "WHERE a.user_id=s.user_id AND a.kind='pattern' AND a.slug=? AND a.name=?))",
            (slug, backdrop or "", slug, slug, pattern or ""),
        )
        return [r["user_id"] for r in rows]

    # ---------- маркет: выгодные лоты ----------
    async def get_resale_sub(self, user_id: int) -> dict:
        row = await self.fetchone("SELECT * FROM resale_subs WHERE user_id=?", (user_id,))
        if not row:
            return {"user_id": user_id, "enabled": 0, "currency": "any", "discount": 20,
                    "all_collections": 1}
        return dict(row)

    async def set_resale_sub(self, user_id: int, **fields: Any) -> None:
        sub = await self.get_resale_sub(user_id)
        sub.update(fields)
        await self.execute(
            "INSERT INTO resale_subs(user_id, enabled, currency, discount, all_collections) "
            "VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, "
            "currency=excluded.currency, discount=excluded.discount, "
            "all_collections=excluded.all_collections",
            (user_id, int(sub["enabled"]), sub["currency"], int(sub["discount"]),
             int(sub["all_collections"])),
        )

    async def get_resale_collections(self, user_id: int) -> set[str]:
        rows = await self.fetchall("SELECT slug FROM resale_sub_collections WHERE user_id=?",
                                   (user_id,))
        return {r["slug"] for r in rows}

    async def toggle_resale_collection(self, user_id: int, slug: str) -> None:
        if slug in await self.get_resale_collections(user_id):
            await self.execute("DELETE FROM resale_sub_collections WHERE user_id=? AND slug=?",
                               (user_id, slug))
        else:
            await self.execute("INSERT OR IGNORE INTO resale_sub_collections(user_id, slug) "
                               "VALUES(?,?)", (user_id, slug))

    async def clear_resale_collections(self, user_id: int) -> None:
        await self.execute("DELETE FROM resale_sub_collections WHERE user_id=?", (user_id,))

    async def resale_wanted_slugs(self) -> set[str] | None:
        """None — нужны все коллекции; иначе — набор нужных (пустой — никто не подписан)."""
        if await self.fetchone("SELECT 1 FROM resale_subs WHERE enabled=1 AND all_collections=1"):
            return None
        rows = await self.fetchall(
            "SELECT DISTINCT c.slug FROM resale_sub_collections c "
            "JOIN resale_subs s ON s.user_id=c.user_id WHERE s.enabled=1"
        )
        return {r["slug"] for r in rows}

    async def resale_candidates(self, slug: str, model: str | None, backdrop: str | None,
                                pattern: str | None) -> list[aiosqlite.Row]:
        """Подписчики маркета, которым подходит лот по коллекции, фону и узору."""
        def attr(kind: str, attr_slug: str) -> str:
            return (f"(NOT EXISTS (SELECT 1 FROM gift_sub_attrs a WHERE a.user_id=s.user_id "
                    f"AND a.kind='{kind}' AND a.slug=?) OR EXISTS (SELECT 1 FROM gift_sub_attrs a "
                    f"WHERE a.user_id=s.user_id AND a.kind='{kind}' AND a.slug=? AND a.name=?))")
        sql = ("SELECT s.user_id, s.currency, s.discount FROM resale_subs s WHERE s.enabled=1 "
               "AND (s.all_collections=1 OR s.user_id IN "
               "(SELECT user_id FROM resale_sub_collections WHERE slug=?)) "
               f"AND {attr('r_backdrop', '*')} AND {attr('r_pattern', slug)}")
        return await self.fetchall(sql, (slug, "*", "*", backdrop or "", slug, slug, pattern or ""))

    async def count_user_attrs(self, user_id: int, kind: str) -> int:
        return await self.scalar(
            "SELECT COUNT(*) FROM gift_sub_attrs WHERE user_id=? AND kind=?", (user_id, kind)
        ) or 0

    # ---------- фоны и узоры подарков ----------
    async def save_gift_attr(self, slug: str, kind: str, name: str, rarity: str,
                             sort_key: int) -> None:
        await self.execute(
            "INSERT INTO gift_attrs(slug, kind, name, rarity, sort_key) VALUES(?,?,?,?,?) "
            "ON CONFLICT(slug, kind, name) DO UPDATE SET rarity=excluded.rarity, "
            "sort_key=excluded.sort_key",
            (slug, kind, name, rarity, sort_key),
        )

    async def slugs_with_attrs(self) -> set[str]:
        rows = await self.fetchall("SELECT DISTINCT slug FROM gift_attrs WHERE kind='model'")
        return {r["slug"] for r in rows}

    async def list_backdrops(self) -> list[aiosqlite.Row]:
        """Все известные фоны (они общие для коллекций), от редких к частым."""
        return await self.fetchall(
            "SELECT name, MIN(sort_key) AS sort_key, MIN(rarity) AS rarity FROM gift_attrs "
            "WHERE kind='backdrop' GROUP BY name ORDER BY sort_key, name"
        )

    async def list_patterns(self, slug: str) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT name, rarity, sort_key FROM gift_attrs WHERE kind='pattern' AND slug=? "
            "ORDER BY sort_key, name", (slug,)
        )

    async def get_user_gift_attrs(self, user_id: int, kind: str, slug: str = "*") -> set[str]:
        rows = await self.fetchall(
            "SELECT name FROM gift_sub_attrs WHERE user_id=? AND kind=? AND slug=?",
            (user_id, kind, slug),
        )
        return {r["name"] for r in rows}

    async def count_user_patterns(self, user_id: int) -> int:
        return await self.scalar(
            "SELECT COUNT(*) FROM gift_sub_attrs WHERE user_id=? AND kind='pattern'", (user_id,)
        ) or 0

    async def toggle_user_gift_attr(self, user_id: int, kind: str, slug: str, name: str) -> None:
        if name in await self.get_user_gift_attrs(user_id, kind, slug):
            await self.execute(
                "DELETE FROM gift_sub_attrs WHERE user_id=? AND kind=? AND slug=? AND name=?",
                (user_id, kind, slug, name),
            )
        else:
            await self.execute(
                "INSERT OR IGNORE INTO gift_sub_attrs(user_id, kind, slug, name) VALUES(?,?,?,?)",
                (user_id, kind, slug, name),
            )

    async def clear_user_gift_attrs(self, user_id: int, kind: str, slug: str | None = None) -> None:
        if slug is None:
            await self.execute("DELETE FROM gift_sub_attrs WHERE user_id=? AND kind=?",
                               (user_id, kind))
        else:
            await self.execute("DELETE FROM gift_sub_attrs WHERE user_id=? AND kind=? AND slug=?",
                               (user_id, kind, slug))

    async def wanted_gift_slugs(self) -> set[str] | None:
        """None — нужны все коллекции; иначе — набор нужных."""
        if await self.fetchone("SELECT 1 FROM gift_subs WHERE enabled=1 AND all_collections=1"):
            return None
        rows = await self.fetchall(
            "SELECT DISTINCT c.slug FROM gift_sub_collections c "
            "JOIN gift_subs s ON s.user_id=c.user_id WHERE s.enabled=1"
        )
        return {r["slug"] for r in rows}

    # ---------- юзернеймы ----------
    async def list_user_usernames(self, user_id: int) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM username_watch WHERE user_id=? ORDER BY created_at", (user_id,)
        )

    async def add_username_watch(self, user_id: int, username: str) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO username_watch(user_id, username, status, created_at) "
            "VALUES(?,?,?,?)",
            (user_id, username.lower(), "unknown", now()),
        )

    async def remove_username_watch(self, user_id: int, username: str) -> None:
        await self.execute(
            "DELETE FROM username_watch WHERE user_id=? AND username=?", (user_id, username.lower())
        )

    async def all_watched_usernames(self) -> list[str]:
        rows = await self.fetchall("SELECT DISTINCT username FROM username_watch")
        return [r["username"] for r in rows]

    async def watchers_of(self, username: str) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM username_watch WHERE username=?", (username.lower(),)
        )

    async def set_watch_status(self, user_id: int, username: str, status: str) -> None:
        await self.execute(
            "UPDATE username_watch SET status=? WHERE user_id=? AND username=?",
            (status, user_id, username.lower()),
        )

    # ---------- статистика ----------
    async def stats(self) -> dict[str, int]:
        q = {
            "users": "SELECT COUNT(*) FROM users WHERE started=1",
            "banned": "SELECT COUNT(*) FROM users WHERE is_banned=1",
            "groups": "SELECT COUNT(*) FROM chats WHERE active=1 AND type IN ('group','supergroup')",
            "business": "SELECT COUNT(*) FROM business_connections WHERE enabled=1",
            "gift_subs": "SELECT COUNT(*) FROM gift_subs WHERE enabled=1",
            "usernames": "SELECT COUNT(*) FROM username_watch",
            "games": "SELECT COUNT(*) FROM games WHERE status='active'",
        }
        return {k: await self.scalar(sql) or 0 for k, sql in q.items()}

    async def started_user_ids(self) -> list[int]:
        rows = await self.fetchall("SELECT id FROM users WHERE started=1 AND is_banned=0")
        return [r["id"] for r in rows]
