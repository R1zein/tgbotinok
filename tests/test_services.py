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
    # имя без профиля — показываем имя, а не «скрыт»
    named = unique(6, 6)
    named.gift.owner_id = None
    named.gift.owner_name = "Вася"
    t2 = format_gift(named.gift, [], [])
    assert "Вася (без ссылки на профиль)" in t2 and "скрыт" not in t2
    # Telegram не раскрыл ничего — честно пишем, что скрыт
    hidden = unique(7, 7)
    hidden.gift.owner_id = None
    assert "скрыт владельцем" in format_gift(hidden.gift, [], [])


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
    # занят <-> Fragment — одно и то же «недоступен», без уведомлений
    await w.apply("freeone", "fragment")
    await w.apply("freeone", "taken")
    assert bot.sent == []
    await w.apply("freeone", "free")
    assert "освободился" in bot.sent[-1][1]
    await w.apply("freeone", "taken")
    assert "заняли" in bot.sent[-1][1]
    await w.apply("freeone", "fragment")
    assert len(bot.sent) == 2

    # дребезг: «недоступен» принимается только после двух одинаковых проверок подряд
    assert w.confirmed("x1", "fragment") is False
    assert w.confirmed("x1", "taken") is False   # сменилось — счёт заново
    assert w.confirmed("x1", "taken") is True
    assert w.confirmed("x1", "free") is True     # «свободен» — сразу
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


def lot(num, stars=None, ton=None, model="Gold", doc=77, ton_only=False, backdrop="Black", bd_id=1):
    attrs = [
        types.StarGiftAttributeModel(name=model, document=types.DocumentEmpty(id=doc),
                                     rarity=types.StarGiftAttributeRarity(permille=15)),
        types.StarGiftAttributeBackdrop(name=backdrop, backdrop_id=bd_id, center_color=0, edge_color=0,
                                        pattern_color=0, text_color=0,
                                        rarity=types.StarGiftAttributeRarity(permille=10)),
    ]
    amounts = []
    if stars is not None:
        amounts.append(types.StarsAmount(amount=stars, nanos=0))
    if ton is not None:
        amounts.append(types.StarsTonAmount(amount=int(ton * 1_000_000_000)))
    return types.StarGiftUnique(id=num, gift_id=10, title="Plush Pepe", slug=f"PlushPepe-{num}",
                                num=num, attributes=attrs, availability_issued=100,
                                availability_total=5000, resell_amount=amounts,
                                resale_ton_only=ton_only)


class MarketUB:
    ready = True

    def __init__(self):
        self.listings = [lot(1, 100), lot(2, 120), lot(3, 500, model="Silver", doc=88)]

    async def call(self, req):
        assert isinstance(req, functions.payments.GetResaleStarGiftsRequest)
        items = list(self.listings)
        for a in req.attributes or []:
            if isinstance(a, types.StarGiftAttributeIdModel):
                items = [g for g in items if g.attributes[0].document.id == a.document_id]
            elif isinstance(a, types.StarGiftAttributeIdBackdrop):
                items = [g for g in items if g.attributes[1].backdrop_id == a.backdrop_id]
        if req.sort_by_price:
            items.sort(key=lambda g: g.resell_amount[0].amount)
        else:
            items = items[::-1]  # новые первыми
        return types.payments.ResaleStarGifts(count=len(items), gifts=items[:req.limit],
                                              chats=[], users=[])


async def test_resale_deals(tmp_path):
    from bot.services.resale import ResaleWatcher
    db = Database(str(tmp_path / "r.db"))
    await db.connect()
    try:
        await db.execute("INSERT INTO gift_collections(slug, title, gift_id, status) "
                         "VALUES('PlushPepe','Plush Pepe',10,'ok')")
        ub, bot = MarketUB(), FakeBot()
        w = ResaleWatcher(bot, db, ub, 30)
        await db.set_resale_sub(1, enabled=1, discount=20)              # по модели
        await db.set_resale_sub(2, enabled=1, discount=40)              # порог выше
        await db.set_resale_sub(3, enabled=1, currency="ton")           # лоты только за звёзды
        await db.set_resale_sub(4, enabled=1, discount=10)
        await db.toggle_user_gift_attr(4, "r_backdrop", "*", "White")   # фильтр по фону
        await w.tick()
        assert bot.sent == []  # первый проход только запоминает текущие лоты

        ub.listings.append(lot(4, 70))  # Gold/Black за 70; следующий Gold — 100 → −30%
        await w.tick()
        assert [c for c, _ in bot.sent] == [1]
        assert "#4" in bot.sent[0][1] and "этой модели на <b>30%" in bot.sent[0][1]

        # Silver на белом фоне за 50: по модели следующий Silver — 500 (−90%),
        # для фильтра по фону белых других нет — подписчику 4 сравнивать не с чем
        ub.listings.append(lot(7, 50, model="Silver", doc=88, backdrop="White", bd_id=2))
        await w.tick()
        assert sorted(c for c, _ in bot.sent[1:]) == [1, 2]
        # ещё один белый — Gold за 30: подписчик 4 сравнивает по фону (следующий белый — 50)
        bot.sent.clear()
        ub.listings.append(lot(8, 30, backdrop="White", bd_id=2))
        await w.tick()
        by_user = dict(bot.sent)
        assert 4 in by_user and "с тем же фоном на <b>40%" in by_user[4]
        assert "этой модели" in by_user[1]  # Gold: следующий Gold — 70 → −57%
    finally:
        await db.close()
