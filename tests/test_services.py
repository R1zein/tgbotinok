from telethon.errors import UsernamePurchaseAvailableError
from telethon.tl import functions, types

from bot.db import Database
from bot.services.gifts import GiftWatcher, format_gift, slug_base
from bot.services.usernames import UsernameWatcher, normalize_username


def unique(num, issued, owner=777):
    attrs = [
        types.StarGiftAttributeModel(name="Gold", document=types.DocumentEmpty(id=1),
                                     rarity=types.StarGiftAttributeRarity(permille=15)),
        types.StarGiftAttributeBackdrop(name="Black", backdrop_id=1, center_color=0, edge_color=0,
                                        pattern_color=0, text_color=0,
                                        rarity=types.StarGiftAttributeRarityLegendary()),
        types.StarGiftAttributePattern(name="Stars", document=types.DocumentEmpty(id=2),
                                       rarity=types.StarGiftAttributeRarity(permille=5)),
    ]
    gift = types.StarGiftUnique(id=num, gift_id=10, title="Plush Pepe", slug=f"PlushPepe-{num}",
                                num=num, attributes=attrs, availability_issued=issued,
                                availability_total=5000, owner_id=types.PeerUser(owner))
    users = [types.User(id=owner, first_name="Pavel", username="durov")]
    return types.payments.UniqueStarGift(gift=gift, chats=[], users=users)


class FakeUB:
    ready = True

    def __init__(self, issued):
        self.issued = issued
        self.calls = []

    async def call(self, req):
        self.calls.append(req)
        if isinstance(req, functions.payments.GetStarGiftsRequest):
            g = types.StarGift(id=10, sticker=types.DocumentEmpty(id=3), stars=100,
                               convert_stars=80, upgrade_stars=50, title="Plush Pepe")
            old = types.StarGift(id=11, sticker=types.DocumentEmpty(id=4), stars=15, convert_stars=13)
            return types.payments.StarGifts(hash=0, gifts=[g, old], chats=[], users=[])
        if isinstance(req, functions.payments.GetUniqueStarGiftRequest):
            num = int(req.slug.rsplit("-", 1)[1])
            if num > self.issued:
                from telethon.errors import RPCError
                raise RPCError(None, "STARGIFT_SLUG_INVALID", 400)
            return unique(num, self.issued)
        if isinstance(req, functions.payments.GetStarGiftUpgradeAttributesRequest):
            return types.payments.StarGiftUpgradeAttributes(attributes=[
                types.StarGiftAttributeBackdrop(name=n, backdrop_id=i, center_color=0, edge_color=0,
                                                pattern_color=0, text_color=0,
                                                rarity=types.StarGiftAttributeRarity(permille=p))
                for i, (n, p) in enumerate([("Black", 10), ("White", 25)])
            ] + [
                types.StarGiftAttributePattern(name=n, document=types.DocumentEmpty(id=9),
                                               rarity=types.StarGiftAttributeRarity(permille=p))
                for n, p in [("Stars", 5), ("Hearts", 12)]
            ])
        if isinstance(req, functions.account.CheckUsernameRequest):
            if req.username == "onfragment":
                raise UsernamePurchaseAvailableError(None)
            return req.username == "freeone"
        raise AssertionError(req)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


def test_slug_and_format():
    assert slug_base("Durov's Cap") == "DurovsCap"
    assert slug_base("Jack-in-the-Box") == "JackintheBox"
    res = unique(5, 5)
    text = format_gift(res.gift, res.users, res.chats)
    assert "Plush Pepe #5" in text and "@durov" in text and "1.5%" in text
    assert "легендарная" in text and "t.me/nft/PlushPepe-5" in text


async def test_gift_watcher(tmp_path):
    db = Database(str(tmp_path / "g.db"))
    await db.connect()
    ub, bot = FakeUB(issued=3), FakeBot()
    w = GiftWatcher(bot, db, ub, 30)
    await db.set_gift_sub(1, enabled=1, all_collections=1)
    await db.set_gift_sub(2, enabled=1, all_collections=0)
    await db.toggle_user_gift_collection(2, "Other")
    await w.tick()  # загрузка коллекций + инициализация last_num=3, без уведомлений
    cols = await db.list_gift_collections()
    assert [c["slug"] for c in cols] == ["PlushPepe"] and cols[0]["last_num"] == 3
    assert bot.sent == []
    ub.issued = 5
    await w.tick()
    assert [c for c, _ in bot.sent] == [1, 1]  # пользователь 2 подписан на другую коллекцию
    assert "#4" in bot.sent[0][1] and "#5" in bot.sent[1][1]
    assert (await db.list_gift_collections())[0]["last_num"] == 5
    await w.tick()
    assert len(bot.sent) == 2
    await db.close()


async def test_username_watcher(tmp_path):
    db = Database(str(tmp_path / "u.db"))
    await db.connect()
    ub, bot = FakeUB(0), FakeBot()
    w = UsernameWatcher(bot, db, ub, 0, 60)
    taken = {"durov"}

    async def web(name):
        return name in taken

    w.web_taken = web
    assert await w.check("durov") == "taken"
    assert await w.check("freeone") == "free"
    assert await w.check("onfragment") == "fragment"
    assert normalize_username("https://t.me/PavelDurov") == "paveldurov"
    assert normalize_username("@ab") is None and normalize_username("a__bc") is None

    await db.add_username_watch(9, "freeone")
    await w.apply("freeone", "taken")  # unknown -> taken: тихо
    assert bot.sent == []
    await w.apply("freeone", "free")
    assert "освободился" in bot.sent[-1][1]
    await w.apply("freeone", "taken")
    assert "заняли" in bot.sent[-1][1]
    await db.close()


async def test_gift_filters_backdrop_pattern(tmp_path):
    db = Database(str(tmp_path / "f.db"))
    await db.connect()
    ub, bot = FakeUB(issued=3), FakeBot()
    w = GiftWatcher(bot, db, ub, 30)
    for uid in (1, 2, 3, 4):
        await db.set_gift_sub(uid, enabled=1, all_collections=1)
    await db.toggle_user_gift_attr(2, "backdrop", "*", "White")          # фон не совпадёт
    await db.toggle_user_gift_attr(3, "backdrop", "*", "Black")          # совпадёт
    await db.toggle_user_gift_attr(4, "pattern", "PlushPepe", "Hearts")  # узор не совпадёт
    await w.tick()
    # варианты фонов и узоров загружены и отсортированы от редких к частым
    assert [r["name"] for r in await db.list_backdrops()] == ["Black", "White"]
    assert [r["name"] for r in await db.list_patterns("PlushPepe")] == ["Stars", "Hearts"]
    ub.issued = 4
    await w.tick()  # подарок #4: фон Black, узор Stars
    assert sorted(c for c, _ in bot.sent) == [1, 3]
    await db.close()
