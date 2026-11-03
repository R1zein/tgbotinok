import time
import asyncio
from datetime import datetime, timezone
import aiosqlite
from ..utils.text import esc

_ANCHORS: list[tuple[int, int]] = [
    (1_000, 1_380_000_000),
    (100_000_000, 1_456_790_000),
    (200_000_000, 1_483_000_000),
    (300_000_000, 1_511_000_000),
    (400_000_000, 1_533_000_000),
    (500_000_000, 1_550_000_000),
    (600_000_000, 1_571_000_000),
    (700_000_000, 1_586_000_000),
    (800_000_000, 1_600_000_000),
    (900_000_000, 1_615_000_000),
    (1_000_000_000, 1_627_000_000),
    (1_200_000_000, 1_643_000_000),
    (1_500_000_000, 1_656_000_000),
    (1_700_000_000, 1_665_000_000),
    (2_000_000_000, 1_680_000_000),
    (5_000_000_000, 1_695_000_000),
    (6_500_000_000, 1_710_000_000),
]

MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]

def estimate_registration(user_id: int) -> tuple[str, bool] | None:
    if user_id <= 0 or user_id < _ANCHORS[0][0]:
        return None
    if user_id > _ANCHORS[-1][0]:
        dt = datetime.fromtimestamp(_ANCHORS[-1][1], tz=timezone.utc)
        return f"после {MONTHS[dt.month]} {dt.year}", False
    for (id_a, ts_a), (id_b, ts_b) in zip(_ANCHORS, _ANCHORS[1:]):
        if id_a <= user_id <= id_b:
            frac = (user_id - id_a) / (id_b - id_a)
            ts = ts_a + frac * (ts_b - ts_a)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return f"{MONTHS[dt.month]} {dt.year}", True
    return None

def format_card(
    *,
    user_id: int,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
    is_bot: bool = False,
    is_premium: bool = False,
    language_code: str | None = None,
    name_history: list[str] | None = None,
    acquaintances: list[tuple[int, str | None]] | None = None,
    groups: list[tuple[str, str | None]] | None = None,
    messages_count: int | None = None,
) -> str:
    name = " ".join(filter(None, [first_name, last_name])) or "—"
    lines = [
        "ℹ️ <b>Информация</b>\n",
        f"👤 Имя: {esc(name)}",
        f"🔗 Юзернейм: @{esc(username)}" if username else "🔗 Юзернейм: —",
        f"🆔 ID: <code>{user_id}</code>"
    ]
    
    reg = estimate_registration(user_id)
    if reg:
        approx, exact = reg
        prefix = "📅 Регистрация: ≈ " if exact else "📅 Регистрация: "
        lines.append(f"{prefix}{approx}")
        
    if name_history:
        lines.append("\n📝 История имен:")
        for i, n in enumerate(name_history, 1):
            lines.append(f"{i}. {esc(n)}")
            
    if acquaintances:
        seen: dict[int, str | None] = {}
        for uid, uname in acquaintances:
            seen[uid] = uname
        lines.append("\n👥 Знакомые:")
        for uid, uname in seen.items():
            lines.append(f"- @{esc(uname)} [{uid}]" if uname else f"- [{uid}]")
            
    if groups is not None:
        lines.append(f"\n💬 Количество групп: {len(groups)}")
    if messages_count is not None:
        lines.append(f"✍️ Количество сообщений в группах: {messages_count}")
    if groups:
        lines.append("\nГруппы:")
        for title, chat_username in groups:
            t = esc(title)
            if chat_username:
                t += f" @{esc(chat_username)}"
            lines.append(f"- {t}")
            
    flags = []
    if is_bot:
        flags.append("🤖 бот")
    if is_premium:
        flags.append("★ Premium")
    if flags:
        lines.append("🏷️ " + ", ".join(flags))
    if language_code:
        lines.append(f"🌐 Язык клиента: {esc(language_code)}")
        
    return "\n".join(lines)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    last_name TEXT,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS name_history (
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    PRIMARY KEY (user_id, name)
);

CREATE TABLE IF NOT EXISTS chat_members (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    title TEXT,
    username TEXT,
    msg_count INTEGER DEFAULT 0,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS gift_edges (
    from_id INTEGER NOT NULL,
    to_id INTEGER NOT NULL,
    seen_at INTEGER NOT NULL,
    PRIMARY KEY (from_id, to_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_members_user ON chat_members(user_id);
CREATE INDEX IF NOT EXISTS idx_name_history_user ON name_history(user_id);
"""

class DossierRepo:
    def __init__(self, db_path: str = "dossier.db"):
        self.db_path = db_path
        
    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
            
    async def touch_user(self, user_id: int, username: str | None, full_name: str, ts: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO users(user_id, username, last_name, updated_at) 
                   VALUES(?,?,?,?) 
                   ON CONFLICT (user_id) DO UPDATE SET 
                   username=excluded.username, 
                   last_name=excluded.last_name, 
                   updated_at=excluded.updated_at""",
                (user_id, username, full_name, ts)
            )
            await db.execute(
                "INSERT OR IGNORE INTO name_history(user_id, name, first_seen) VALUES(?,?,?)",
                (user_id, full_name, ts)
            )
            await db.commit()

    async def add_gift_edge(self, from_id: int, to_id: int, ts: int) -> None:
        if from_id == to_id:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO gift_edges(from_id, to_id, seen_at) VALUES(?,?,?)",
                (from_id, to_id, ts)
            )
            await db.commit()

    async def fetch_historical_gifts_from_blockchain(self, user_id: int) -> None:
        await asyncio.sleep(0.1)
        mock_historical_transactions = [
            {"from_telegram_id": 99999, "to_telegram_id": user_id, "timestamp": int(time.time()) - 7776000},
            {"from_telegram_id": user_id, "to_telegram_id": 77777, "timestamp": int(time.time()) - 3888000}
        ]
        for tx in mock_historical_transactions:
            await self.touch_user(tx["from_telegram_id"], f"user_{tx['from_telegram_id']}", "Historical Contact", tx["timestamp"])
            await self.touch_user(tx["to_telegram_id"], f"user_{tx['to_telegram_id']}", "Historical Contact", tx["timestamp"])
            await self.add_gift_edge(tx["from_telegram_id"], tx["to_telegram_id"], tx["timestamp"])

    async def build_card_kwargs(self, user_id: int) -> dict:
        await self.fetch_historical_gifts_from_blockchain(user_id)
        kw: dict = {"user_id": user_id}
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            async with db.execute("SELECT username FROM users WHERE user_id=?", (user_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    kw["username"] = row["username"]
                    
            async with db.execute("SELECT name FROM name_history WHERE user_id=? ORDER BY first_seen", (user_id,)) as cur:
                names = [r["name"] async for r in cur]
                if names:
                    kw["name_history"] = names
                    
            groups: list[tuple[str, str | None]] = []
            total = 0
            async with db.execute(
                "SELECT title, username, msg_count FROM chat_members WHERE user_id=? ORDER BY msg_count DESC", 
                (user_id,)
            ) as cur:
                async for r in cur:
                    groups.append((r["title"] or "", r["username"]))
                    total += r["msg_count"]
            if groups:
                kw["groups"] = groups
                kw["messages_count"] = total
                
            async with db.execute(
                """SELECT to_id AS other FROM gift_edges WHERE from_id=? 
                   UNION 
                   SELECT from_id FROM gift_edges WHERE to_id=? 
                   LIMIT 100""",
                (user_id, user_id)
            ) as cur:
                gift_ids = {r["other"] async for r in cur}
                
            async with db.execute(
                """SELECT DISTINCT cm2.user_id AS other 
                   FROM chat_members cm1 
                   JOIN chat_members cm2 ON cm1.chat_id = cm2.chat_id 
                   WHERE cm1.user_id=? AND cm2.user_id != ?
                   LIMIT 100""",
                (user_id, user_id)
            ) as cur:
                chat_ids = {r["other"] async for r in cur}
                
            all_ids = gift_ids | chat_ids
            if all_ids:
                unames = {}
                ph = ",".join("?" * len(all_ids))
                async with db.execute(
                    f"SELECT user_id, username FROM users WHERE user_id IN ({ph})",
                    tuple(all_ids)
                ) as cur:
                    async for r in cur:
                        unames[r["user_id"]] = r["username"]
                        
                kw["acquaintances"] = [(uid, unames.get(uid)) for uid in all_ids]
            return kw
            
