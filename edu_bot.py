import logging
import os
import json
import base64
import httpx






from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = os.environ["ADMIN_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CARD_NUMBER = "2204 3204 6631 3501"
PRICE_PER_SUBJECT = 690
PROMO_PRICE = 1790
PROMO_SUBJECTS_COUNT = 4
CONTACT_LINK = "https://t.me/nepapinomoloko"
REFERRAL_REQUIRED = 20

# ── Защита от ботов ────────────────────────────────────────────
import time
import random

# Минимальный Telegram ID для "старых" аккаунтов.
# Аккаунты с очень большим ID (> ~7 млрд) — как правило свежезарегистрированные боты.
# Можно подобрать порог под себя; сейчас ~7.5B соответствует ~2024 году.
MIN_TRUSTED_USER_ID = 1_000_000        # ниже этого — очень старые аккаунты (доверенные)
MAX_BOT_SUSPICIOUS_ID = 8_000_000_000  # выше этого — подозрительно новые

# Максимум новых рефералов от одного реферера за сутки
MAX_REFERRALS_PER_DAY = 10

# ── Состояния ──────────────────────────────────────────────────
(SELECT_REGION, SELECT_SUBJECT, CONFIRM_ORDER, WAIT_PAYMENT, CAPTCHA_STATE) = range(5)

REGIONS = [
    "Республика Адыгея", "Республика Алтай", "Республика Башкортостан",
    "Республика Бурятия", "Республика Дагестан", "Республика Ингушетия",
    "Кабардино-Балкарская Республика", "Республика Калмыкия",
    "Карачаево-Черкесская Республика", "Республика Карелия",
    "Республика Коми", "Республика Крым", "Республика Марий Эл",
    "Республика Мордовия", "Республика Саха (Якутия)", "Республика Северная Осетия — Алания",
    "Республика Татарстан", "Республика Тыва", "Удмуртская Республика",
    "Республика Хакасия", "Чеченская Республика", "Чувашская Республика",
    "Алтайский край", "Забайкальский край", "Камчатский край",
    "Краснодарский край", "Красноярский край", "Пермский край",
    "Приморский край", "Ставропольский край", "Хабаровский край",
    "Амурская область", "Архангельская область", "Астраханская область",
    "Белгородская область", "Брянская область", "Владимирская область",
    "Волгоградская область", "Вологодская область", "Воронежская область",
    "Ивановская область", "Иркутская область", "Калининградская область",
    "Калужская область", "Кемеровская область", "Кировская область",
    "Костромская область", "Курганская область", "Курская область",
    "Ленинградская область", "Липецкая область", "Магаданская область",
    "Московская область", "Мурманская область", "Нижегородская область",
    "Новгородская область", "Новосибирская область", "Омская область",
    "Оренбургская область", "Орловская область", "Пензенская область",
    "Псковская область", "Ростовская область", "Рязанская область",
    "Самарская область", "Саратовская область", "Сахалинская область",
    "Свердловская область", "Смоленская область", "Тамбовская область",
    "Тверская область", "Томская область", "Тульская область",
    "Тюменская область", "Ульяновская область", "Челябинская область",
    "Ярославская область", "Москва", "Санкт-Петербург", "Севастополь",
    "Еврейская автономная область", "Ненецкий автономный округ",
    "Ханты-Мансийский автономный округ — Югра", "Чукотский автономный округ",
    "Ямало-Ненецкий автономный округ",
]

SUBJECTS = [
    "📐 Физика",
    "🧪 Химия",
    "🔬 Биология",
    "📚 Литература",
    "🌍 География",
    "🏛 История",
    "💻 Информатика",
    "📖 Обществознание",
    "🇬🇧 Английский язык",
    "📝 Русский язык",
    "➗ Математика",
]

IDX_RUSSIAN = 9
IDX_MATH = 10

USERS_FILE = "users.json"


# ══════════════════════════════════════════════════════════════
# РАБОТА С ПОЛЬЗОВАТЕЛЯМИ
# ══════════════════════════════════════════════════════════════

def load_users() -> dict:
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_users(users: dict):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def register_user(user, referred_by: int = None) -> bool:
    users = load_users()
    uid = str(user.id)
    if uid not in users:
        users[uid] = {
            "id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "referred_by": referred_by,
            "referral_count": 0,
            "free_pack_used": False,
            "purchases": [],
            "captcha_passed": False,  # ← реферал засчитается только после капчи
        }
        # НЕ начисляем реферал сразу — ждём капчу
        if referred_by:
            users[uid]["pending_referrer"] = referred_by
        save_users(users)
        return True
    return False


# ── Анти-бот хелперы ───────────────────────────────────────────

def is_suspicious_account(user) -> bool:
    """Проверяет, подозрительный ли аккаунт."""
    # Нет имени вообще
    if not user.first_name and not user.last_name:
        return True
    # Очень новый аккаунт (большой ID)
    if user.id > MAX_BOT_SUSPICIOUS_ID:
        return True
    return False


def can_earn_referral_today(referrer_id: int) -> bool:
    """Проверяет, не превышен ли лимит рефералов за сутки."""
    users = load_users()
    uid = str(referrer_id)
    if uid not in users:
        return False
    today = time.strftime("%Y-%m-%d")
    daily = users[uid].get("daily_referrals", {})
    count_today = daily.get(today, 0)
    return count_today < MAX_REFERRALS_PER_DAY


def increment_daily_referral(referrer_id: int):
    """Увеличивает счётчик рефералов за сутки."""
    users = load_users()
    uid = str(referrer_id)
    if uid not in users:
        return
    today = time.strftime("%Y-%m-%d")
    if "daily_referrals" not in users[uid]:
        users[uid]["daily_referrals"] = {}
    users[uid]["daily_referrals"][today] = users[uid]["daily_referrals"].get(today, 0) + 1
    save_users(users)


def store_pending_referral(new_user_id: int, referrer_id: int):
    """Сохраняет ожидающий реферал (до прохождения капчи)."""
    users = load_users()
    uid = str(new_user_id)
    if uid in users:
        users[uid]["pending_referrer"] = referrer_id
        save_users(users)


def confirm_pending_referral(user_id: int) -> int | None:
    """Засчитывает реферал после прохождения капчи. Возвращает ID реферера или None."""
    users = load_users()
    uid = str(user_id)
    if uid not in users:
        return None
    referrer_id = users[uid].pop("pending_referrer", None)
    if not referrer_id:
        return None
    ref_uid = str(referrer_id)
    if ref_uid not in users:
        save_users(users)
        return None
    # Проверяем лимит дня
    if not can_earn_referral_today(referrer_id):
        save_users(users)
        return None
    users[ref_uid]["referral_count"] = users[ref_uid].get("referral_count", 0) + 1
    save_users(users)
    increment_daily_referral(referrer_id)
    return referrer_id


def make_captcha() -> tuple[int, int, str]:
    """Генерирует простую математическую капчу. Возвращает (правильный ответ, вариант1, вариант2)."""
    a = random.randint(2, 9)
    b = random.randint(2, 9)
    correct = a + b
    wrong = correct + random.choice([-2, -1, 1, 2])
    return correct, a, b, wrong


def get_user_data(user_id: int) -> dict:
    users = load_users()
    return users.get(str(user_id), {})


def get_user_referral_count(user_id: int) -> int:
    return get_user_data(user_id).get("referral_count", 0)


def has_free_pack(user_id: int) -> bool:
    u = get_user_data(user_id)
    return u.get("referral_count", 0) >= REFERRAL_REQUIRED and not u.get("free_pack_used", False)


def mark_free_pack_used(user_id: int):
    users = load_users()
    uid = str(user_id)
    if uid in users:
        users[uid]["free_pack_used"] = True
        save_users(users)


def add_purchase(user_id: int, subjects: list, amount: int, region: str):
    """Записывает покупку в историю пользователя."""
    users = load_users()
    uid = str(user_id)
    if uid in users:
        if "purchases" not in users[uid]:
            users[uid]["purchases"] = []
        users[uid]["purchases"].append({
            "subjects": subjects,
            "amount": amount,
            "region": region,
        })
        save_users(users)


# ══════════════════════════════════════════════════════════════
# УВЕДОМЛЕНИЯ АДМИНА
# ══════════════════════════════════════════════════════════════

async def notify_admin(bot, text: str):
    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    except Exception as e:
        logger.error(f"Ошибка уведомления админа: {e}")


def user_tag(user) -> str:
    username = f"@{user.username}" if user.username else f"ID {user.id}"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return f"{name} ({username})"


# ══════════════════════════════════════════════════════════════
# ПОДСЧЁТ КОРЗИНЫ
# ══════════════════════════════════════════════════════════════

def calc_cart(cart: list) -> tuple:
    has_math = IDX_MATH in cart
    has_rus = IDX_RUSSIAN in cart
    if has_math and has_rus and len(cart) >= PROMO_SUBJECTS_COUNT:
        extra = len(cart) - PROMO_SUBJECTS_COUNT
        return PROMO_PRICE + extra * PRICE_PER_SUBJECT, True
    return len(cart) * PRICE_PER_SUBJECT, False


def cart_text(cart: list) -> str:
    if not cart:
        return "🛒 Корзина пуста"
    items = "\n".join(f"  • {SUBJECTS[i]}" for i in cart)
    total, promo = calc_cart(cart)
    promo_line = "\n🔥 Применена акция: Рус + Мат + 2 предмета = 1790 ₽" if promo else ""
    return f"🛒 Корзина ({len(cart)} предм.):\n{items}{promo_line}\n\n💰 Итого: {total} ₽"


def referral_progress_bar(count: int, total: int = REFERRAL_REQUIRED) -> str:
    """Красивый прогресс-бар для реферальной программы."""
    filled = min(count, total)
    bar_len = 10
    filled_blocks = round(filled / total * bar_len)
    bar = "🟩" * filled_blocks + "⬜" * (bar_len - filled_blocks)
    return f"{bar} {count}/{total}"


# ══════════════════════════════════════════════════════════════
# КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════

REGIONS_PER_PAGE = 10


def make_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню с кнопкой личного кабинета."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Купить ответы", callback_data="menu_buy")],
        [InlineKeyboardButton("👤 Личный кабинет", callback_data="menu_cabinet")],
    ])


def make_cabinet_keyboard(has_free: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура личного кабинета."""
    rows = [
        [InlineKeyboardButton("🔗 Реферальная программа", callback_data="cab_referral")],
        [InlineKeyboardButton("📋 История покупок", callback_data="cab_history")],
    ]
    if has_free:
        rows.insert(0, [InlineKeyboardButton("🎁 Использовать бесплатный пакет", callback_data="cab_use_free")])
    rows.append([InlineKeyboardButton("◀️ Главное меню", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def make_referral_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад в кабинет", callback_data="menu_cabinet")],
    ])


def make_regions_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    start = page * REGIONS_PER_PAGE
    end = start + REGIONS_PER_PAGE
    keyboard = []
    for region in REGIONS[start:end]:
        idx = REGIONS.index(region)
        keyboard.append([InlineKeyboardButton(region, callback_data=f"region:{idx}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Назад", callback_data=f"rpage:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{(len(REGIONS)-1)//REGIONS_PER_PAGE+1}", callback_data="noop"))
    if end < len(REGIONS):
        nav.append(InlineKeyboardButton("▶️ Вперёд", callback_data=f"rpage:{page+1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("◀️ Главное меню", callback_data="menu_main")])
    return InlineKeyboardMarkup(keyboard)


def make_subjects_keyboard(cart: list) -> InlineKeyboardMarkup:
    keyboard = []
    for i, subject in enumerate(SUBJECTS):
        mark = "✅ " if i in cart else ""
        keyboard.append([InlineKeyboardButton(f"{mark}{subject}", callback_data=f"subject:{i}")])
    bottom = []
    if cart:
        bottom.append(InlineKeyboardButton(f"🛒 В корзину ({len(cart)})", callback_data="view_cart"))
    keyboard.append(bottom if bottom else [InlineKeyboardButton("ℹ️ Выберите предмет", callback_data="noop")])
    keyboard.append([InlineKeyboardButton("◀️ Назад к регионам", callback_data="back_to_regions")])
    return InlineKeyboardMarkup(keyboard)


def make_cart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Оплатить", callback_data="pay")],
        [InlineKeyboardButton("✏️ Изменить список", callback_data="back_to_subjects")],
        [InlineKeyboardButton("🔄 Очистить корзину", callback_data="clear_cart")],
    ])


# ══════════════════════════════════════════════════════════════
# ПРОВЕРКА ОПЛАТЫ ЧЕРЕЗ CLAUDE
# ══════════════════════════════════════════════════════════════

async def check_payment_with_claude(image_bytes: bytes, region: str, subjects: list, amount: int) -> tuple:
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 500,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
                },
                {
                    "type": "text",
                    "text": (
                        f"Это скриншот перевода денег. Проверь:\n"
                        f"1. Сумма перевода равна {amount} рублей\n"
                        f"2. Статус перевода — успешно / выполнено / отправлено\n"
                        f"3. Номер карты получателя содержит {CARD_NUMBER[-4:]} (последние 4 цифры)\n\n"
                        f"Ответь ТОЛЬКО в формате JSON:\n"
                        f'{{\"ok\": true/false, \"reason\": \"причина на русском\"}}\n'
                        f"Если всё совпадает — ok: true. Если что-то не так — ok: false и укажи причину."
                    ),
                },
            ],
        }],
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            data = resp.json()
            text = data["content"][0]["text"].strip().replace("```json", "").replace("```", "").strip()
            result = json.loads(text)
            return result.get("ok", False), result.get("reason", "")
    except Exception as e:
        logger.error(f"Ошибка Claude API: {e}")
        return False, "Ошибка проверки, попробуйте ещё раз"


# ══════════════════════════════════════════════════════════════
# HELPERS: отрисовка экранов
# ══════════════════════════════════════════════════════════════

async def send_or_edit(query, text: str, markup, photo_caption: bool = False):
    """Универсальная функция: редактирует текст или caption."""
    if query.message.photo:
        try:
            await query.edit_message_caption(caption=text, reply_markup=markup)
        except Exception:
            await query.message.delete()
            await query.message.chat.send_message(text=text, reply_markup=markup)
    else:
        await query.edit_message_text(text, reply_markup=markup)


async def show_main_menu(query, context, user):
    """Показывает главное меню."""
    ref_count = get_user_referral_count(user.id)
    free = has_free_pack(user.id)
    free_line = "\n\n🎁 У вас есть БЕСПЛАТНЫЙ пакет! Зайдите в личный кабинет." if free else ""

    text = (
        "🏠 Главное меню\n\n"
        "📌 Реальные ответы на ОГЭ по всем регионам России\n\n"
        "✅ Более 3 000 довольных учеников\n"
        "✅ Актуально для вашего региона\n"
        "✅ Моментальная доставка после оплаты\n\n"
        "🔥 АКЦИЯ: Математика + Русский + 2 предмета = 1790 ₽\n"
        f"💰 Или 1 предмет за {PRICE_PER_SUBJECT} ₽"
        f"{free_line}"
    )
    await send_or_edit(query, text, make_main_menu_keyboard())


async def show_cabinet(query, context, user):
    """Показывает личный кабинет."""
    u = get_user_data(user.id)
    ref_count = u.get("referral_count", 0)
    free = has_free_pack(user.id)
    purchases = u.get("purchases", [])
    name = u.get("first_name", "") or user.first_name or "Пользователь"
    username_line = f"@{user.username}" if user.username else f"ID: {user.id}"

    free_status = "🎁 Есть бесплатный пакет!" if free else (
        "✅ Пакет использован" if u.get("free_pack_used") else f"🔗 Прогресс: {referral_progress_bar(ref_count)}"
    )

    text = (
        f"👤 Личный кабинет\n"
        f"{'─' * 28}\n"
        f"🙋 {name}  |  {username_line}\n"
        f"{'─' * 28}\n\n"
        f"🛍 Покупок: {len(purchases)}\n"
        f"🔗 Рефералов: {ref_count}\n"
        f"{free_status}\n"
    )
    await send_or_edit(query, text, make_cabinet_keyboard(has_free=free))


async def show_referral(query, context, user):
    """Показывает страницу реферальной программы."""
    u = get_user_data(user.id)
    ref_count = u.get("referral_count", 0)
    remaining = max(0, REFERRAL_REQUIRED - ref_count)
    free = has_free_pack(user.id)
    used = u.get("free_pack_used", False)
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    if free:
        status_block = (
            "🎉 Поздравляем! Вы заработали БЕСПЛАТНЫЙ пакет!\n\n"
            "Нажмите «Использовать бесплатный пакет» в личном кабинете\n"
            "и выберите: Математика + Русский + 2 предмета на ваш выбор."
        )
    elif used:
        status_block = "✅ Бесплатный пакет уже использован. Продолжайте приглашать!"
    else:
        status_block = (
            f"До бесплатного пакета осталось: {remaining} чел.\n"
            f"{referral_progress_bar(ref_count)}\n\n"
            f"🎁 Награда: Математика + Русский + 2 предмета на выбор — бесплатно!"
        )

    text = (
        f"🔗 Реферальная программа\n"
        f"{'─' * 28}\n\n"
        f"Приглашайте друзей по своей ссылке.\n"
        f"За каждого нового пользователя вы получаете +1 реферал.\n\n"
        f"👥 Ваша ссылка:\n"
        f"{link}\n\n"
        f"{'─' * 28}\n"
        f"{status_block}"
    )
    await send_or_edit(query, text, make_referral_keyboard())


# ══════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════

async def captcha_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ответа на капчу."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    try:
        chosen = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        return CAPTCHA_STATE

    correct = context.user_data.get('captcha_answer')

    if chosen == correct:
        # Отмечаем капчу пройденной
        users = load_users()
        uid = str(user.id)
        if uid in users:
            users[uid]["captcha_passed"] = True
            save_users(users)

        # Засчитываем реферал (с проверками)
        referrer_id = confirm_pending_referral(user.id)
        if referrer_id:
            count = get_user_referral_count(referrer_id)
            await notify_admin(
                context.bot,
                f"✅ Реферал засчитан (капча пройдена)!\nПригласил ID {referrer_id}\n"
                f"Новый: {user_tag(user)}\nВсего рефералов: {count}"
            )
            if count >= REFERRAL_REQUIRED:
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=(
                            f"🎉 Вы пригласили {count} человек!\n\n"
                            f"🆓 БЕСПЛАТНЫЙ пакет заработан:\n"
                            f"📝 Русский + ➗ Математика + 2 предмета на выбор!\n\n"
                            f"Зайдите в 👤 Личный кабинет → Использовать бесплатный пакет"
                        )
                    )
                except Exception:
                    pass

        await notify_admin(context.bot, f"✅ {user_tag(user)} прошёл капчу")

        free = has_free_pack(user.id)
        free_line = "\n\n🎁 У вас есть БЕСПЛАТНЫЙ пакет! Зайдите в личный кабинет." if free else ""
        caption = (
            "✅ Проверка пройдена! Добро пожаловать! 👋\n\n"
            "📌 Реальные ответы на ОГЭ по всем регионам России\n\n"
            "✅ Более 3 000 довольных учеников\n"
            "✅ Ответы по всем предметам\n"
            "✅ Актуально для вашего региона\n"
            "✅ Моментальная доставка после оплаты\n\n"
            "🔥 АКЦИЯ: Математика + Русский + 2 предмета = 1790 ₽\n"
            f"💰 Или 1 предмет за {PRICE_PER_SUBJECT} ₽"
            f"{free_line}\n\n"
            "Выберите действие 👇"
        )
        await query.edit_message_text(caption, reply_markup=make_main_menu_keyboard())
        return SELECT_REGION
    else:
        # Неверный ответ — новая капча
        await notify_admin(context.bot, f"❌ {user_tag(user)} не прошёл капчу (ответил {chosen})")
        correct2, a, b, wrong = make_captcha()
        context.user_data['captcha_answer'] = correct2
        buttons = [correct2, wrong]
        random.shuffle(buttons)
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(str(buttons[0]), callback_data=f"captcha:{buttons[0]}"),
            InlineKeyboardButton(str(buttons[1]), callback_data=f"captcha:{buttons[1]}"),
        ]])
        await query.edit_message_text(
            f"❌ Неверно! Попробуйте ещё раз.\n\nСколько будет *{a} + {b}*?",
            parse_mode="Markdown",
            reply_markup=markup
        )
        return CAPTCHA_STATE


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    referred_by = None
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referred_by = int(arg[4:])
                # Нельзя пригласить самого себя
                if referred_by == user.id:
                    referred_by = None
            except ValueError:
                pass

    is_new = register_user(user, referred_by=referred_by)

    context.user_data.clear()
    context.user_data['cart'] = []

    # ── Капча для новых пользователей ──────────────────────────
    udata = get_user_data(user.id)
    captcha_passed = udata.get("captcha_passed", False)

    if not captcha_passed:
        # Подозрительный аккаунт — сообщаем в лог
        if is_suspicious_account(user):
            await notify_admin(
                context.bot,
                f"⚠️ Подозрительный аккаунт при /start\n"
                f"ID: {user.id} | Имя: {user.first_name or '—'} {user.last_name or ''}\n"
                f"Username: @{user.username or 'нет'}"
            )

        correct, a, b, wrong = make_captcha()
        context.user_data['captcha_answer'] = correct

        # Перемешиваем кнопки
        buttons = [correct, wrong]
        random.shuffle(buttons)

        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(str(buttons[0]), callback_data=f"captcha:{buttons[0]}"),
            InlineKeyboardButton(str(buttons[1]), callback_data=f"captcha:{buttons[1]}"),
        ]])

        if is_new:
            ref_text = f"\n🔗 По реферальной ссылке от ID {referred_by}" if referred_by else ""
            await notify_admin(
                context.bot,
                f"👤 Новый пользователь (капча не пройдена)\nID: {user.id}\n"
                f"Имя: {user.first_name or ''} {user.last_name or ''}\n"
                f"Username: @{user.username or 'нет'}{ref_text}"
            )

        await update.message.reply_text(
            f"👋 Добро пожаловать!\n\n"
            f"🔐 Пожалуйста, подтвердите, что вы не бот.\n\n"
            f"Сколько будет *{a} + {b}*?",
            parse_mode="Markdown",
            reply_markup=markup
        )
        return CAPTCHA_STATE

    await notify_admin(context.bot, f"▶️ {user_tag(user)} нажал /start")

    free = has_free_pack(user.id)
    free_line = "\n\n🎁 У вас есть БЕСПЛАТНЫЙ пакет! Зайдите в личный кабинет." if free else ""

    caption = (
        "Добро пожаловать! 👋\n\n"
        "📌 Реальные ответы на ОГЭ по всем регионам России\n\n"
        "✅ Более 3 000 довольных учеников\n"
        "✅ Ответы по всем предметам\n"
        "✅ Актуально для вашего региона\n"
        "✅ Моментальная доставка после оплаты\n\n"
        "🔥 АКЦИЯ: Математика + Русский + 2 предмета = 1790 ₽\n"
        f"💰 Или 1 предмет за {PRICE_PER_SUBJECT} ₽"
        f"{free_line}\n\n"
        "Выберите действие 👇"
    )

    photo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "welcome.png")
    if os.path.exists(photo_path):
        with open(photo_path, "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=caption,
                reply_markup=make_main_menu_keyboard()
            )
    else:
        await update.message.reply_text(caption, reply_markup=make_main_menu_keyboard())
    return SELECT_REGION


# ── Главное меню ────────────────────────────────────────────

async def menu_main_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в главное меню."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    await show_main_menu(query, context, user)
    return SELECT_REGION


async def menu_buy_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажали «Купить ответы» — переходим к выбору региона."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    await notify_admin(context.bot, f"🛒 {user_tag(user)} нажал «Купить ответы»")
    context.user_data['cart'] = []

    caption = (
        "📍 Выберите ваш регион:\n\n"
        "🔥 АКЦИЯ: Математика + Русский + 2 предмета = 1790 ₽\n"
        f"💰 Или 1 предмет за {PRICE_PER_SUBJECT} ₽"
    )
    await send_or_edit(query, caption, make_regions_keyboard(0))
    return SELECT_REGION


# ── Личный кабинет ──────────────────────────────────────────

async def menu_cabinet_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открывает личный кабинет."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    await notify_admin(context.bot, f"👤 {user_tag(user)} открыл личный кабинет")
    await show_cabinet(query, context, user)
    return SELECT_REGION


async def cab_referral_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Страница реферальной программы."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    await notify_admin(context.bot, f"🔗 {user_tag(user)} открыл реферальную программу")
    await show_referral(query, context, user)
    return SELECT_REGION


async def cab_history_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """История покупок."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    u = get_user_data(user.id)
    purchases = u.get("purchases", [])

    if not purchases:
        text = (
            "📋 История покупок\n"
            f"{'─' * 28}\n\n"
            "У вас пока нет покупок.\n"
            "Нажмите «Купить ответы» чтобы сделать первый заказ!"
        )
    else:
        lines = []
        for i, p in enumerate(purchases, 1):
            subj = ", ".join(p.get("subjects", []))
            amt = p.get("amount", 0)
            reg = p.get("region", "—")
            lines.append(f"{i}. {reg}\n   {subj}\n   💰 {amt} ₽")
        text = (
            f"📋 История покупок ({len(purchases)} шт.)\n"
            f"{'─' * 28}\n\n"
            + "\n\n".join(lines)
        )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад в кабинет", callback_data="menu_cabinet")],
    ])
    await send_or_edit(query, text, markup)
    return SELECT_REGION


async def cab_use_free_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает оформление бесплатного пакета."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not has_free_pack(user.id):
        await query.answer("Бесплатный пакет недоступен.", show_alert=True)
        return SELECT_REGION

    await notify_admin(context.bot, f"🎁 {user_tag(user)} начал оформление бесплатного пакета")
    context.user_data['cart'] = []
    context.user_data['is_free_pack'] = True

    caption = (
        "🎁 Бесплатный пакет\n\n"
        "Выберите регион:"
    )
    await send_or_edit(query, caption, make_regions_keyboard(0))
    return SELECT_REGION


# ── Регион ──────────────────────────────────────────────────

async def back_to_regions_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = context.user_data.get('region_page', 0)

    caption = (
        "📍 Выберите ваш регион:\n\n"
        "🔥 АКЦИЯ: Математика + Русский + 2 предмета = 1790 ₽\n"
        f"💰 Или 1 предмет за {PRICE_PER_SUBJECT} ₽"
    )
    await send_or_edit(query, caption, make_regions_keyboard(page))
    return SELECT_REGION


async def region_page_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.replace("rpage:", ""))
    context.user_data['region_page'] = page
    if query.message.photo:
        await query.edit_message_caption(
            caption=query.message.caption,
            reply_markup=make_regions_keyboard(page)
        )
    else:
        await query.edit_message_reply_markup(reply_markup=make_regions_keyboard(page))
    return SELECT_REGION


async def noop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return SELECT_REGION


async def region_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    region_idx = int(query.data.replace("region:", ""))
    region = REGIONS[region_idx]
    context.user_data['region'] = region
    user = update.effective_user

    await notify_admin(context.bot, f"📍 {user_tag(user)} выбрал регион: {region}")

    cart = context.user_data.get('cart', [])
    is_free = context.user_data.get('is_free_pack', False)
    free_note = "\n\n🎁 Бесплатный пакет: выберите Математику + Русский + 2 предмета." if is_free else ""

    text = (
        f"📍 Регион: {region}\n\n"
        f"🔥 АКЦИЯ: Математика + Русский + 2 предмета = 1790 ₽\n"
        f"💰 Или выбери любые предметы по {PRICE_PER_SUBJECT} ₽/шт\n\n"
        f"📚 Выбери предметы в корзину (можно несколько):"
        f"{free_note}"
    )
    await send_or_edit(query, text, make_subjects_keyboard(cart))
    return SELECT_SUBJECT


# ── Предметы / Корзина ──────────────────────────────────────

async def subject_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject_idx = int(query.data.replace("subject:", ""))
    subject = SUBJECTS[subject_idx]
    user = update.effective_user

    cart: list = context.user_data.get('cart', [])
    if subject_idx in cart:
        cart.remove(subject_idx)
        action = "убрал из корзины"
    else:
        cart.append(subject_idx)
        action = "добавил в корзину"
    context.user_data['cart'] = cart

    await notify_admin(context.bot, f"🛒 {user_tag(user)} {action}: {subject} (в корзине: {len(cart)} предм.)")

    region = context.user_data.get('region', '')
    total, promo = calc_cart(cart)
    promo_line = "\n🔥 Акция применена!" if promo else ""
    text = (
        f"📍 Регион: {region}\n\n"
        f"🔥 АКЦИЯ: Математика + Русский + 2 предмета = 1790 ₽\n"
        f"💰 Или выбери любые предметы по {PRICE_PER_SUBJECT} ₽/шт\n\n"
        f"📚 Выбери предметы:\n"
        f"{cart_text(cart)}{promo_line}"
    )
    await send_or_edit(query, text, make_subjects_keyboard(cart))
    return SELECT_SUBJECT


async def view_cart_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    cart = context.user_data.get('cart', [])

    if not cart:
        await query.answer("Корзина пуста! Добавьте предметы.", show_alert=True)
        return SELECT_SUBJECT

    region = context.user_data.get('region', '')
    total, promo = calc_cart(cart)
    is_free = context.user_data.get('is_free_pack', False)
    free_pack = has_free_pack(user.id)

    await notify_admin(context.bot, f"👀 {user_tag(user)} открыл корзину: {len(cart)} предм., {total} ₽")

    if (is_free or free_pack):
        required = {IDX_MATH, IDX_RUSSIAN}
        if required.issubset(set(cart)) and len(cart) >= PROMO_SUBJECTS_COUNT:
            text = (
                f"📍 Регион: {region}\n\n"
                f"{cart_text(cart)}\n\n"
                f"🎁 Этот заказ БЕСПЛАТНЫЙ по реферальной программе!\n"
                f"Нажми «Получить бесплатно»."
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎁 Получить бесплатно", callback_data="free_pack_confirm")],
                [InlineKeyboardButton("✏️ Изменить список", callback_data="back_to_subjects")],
            ])
        else:
            text = (
                f"📍 Регион: {region}\n\n"
                f"{cart_text(cart)}\n\n"
                f"🎁 Бесплатный пакет: добавьте Математику + Русский + минимум 2 предмета."
            )
            markup = make_cart_keyboard()
    else:
        text = f"📍 Регион: {region}\n\n{cart_text(cart)}"
        markup = make_cart_keyboard()

    await send_or_edit(query, text, markup)
    return CONFIRM_ORDER


async def clear_cart_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Корзина очищена")
    user = update.effective_user
    context.user_data['cart'] = []
    await notify_admin(context.bot, f"🗑 {user_tag(user)} очистил корзину")

    region = context.user_data.get('region', '')
    text = (
        f"📍 Регион: {region}\n\n"
        f"🔥 АКЦИЯ: Математика + Русский + 2 предмета = 1790 ₽\n\n"
        f"📚 Выбери предметы:"
    )
    await send_or_edit(query, text, make_subjects_keyboard([]))
    return SELECT_SUBJECT


async def back_to_subjects_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    region = context.user_data.get('region', '')
    cart = context.user_data.get('cart', [])

    text = (
        f"📍 Регион: {region}\n\n"
        f"🔥 АКЦИЯ: Математика + Русский + 2 предмета = 1790 ₽\n\n"
        f"📚 Выбери предметы:\n"
        f"{cart_text(cart)}"
    )
    await send_or_edit(query, text, make_subjects_keyboard(cart))
    return SELECT_SUBJECT


async def free_pack_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    cart = context.user_data.get('cart', [])
    region = context.user_data.get('region', '')
    subjects_names = [SUBJECTS[i] for i in cart]

    mark_free_pack_used(user.id)
    add_purchase(user.id, subjects_names, 0, region)
    context.user_data['is_free_pack'] = False

    await notify_admin(
        context.bot,
        f"🎁 БЕСПЛАТНЫЙ ПАКЕТ (реферальная программа)\n"
        f"👤 {user_tag(user)}\n"
        f"📍 Регион: {region}\n"
        f"📚 Предметы: {', '.join(subjects_names)}"
    )

    text = (
        f"🎉 Бесплатный пакет активирован!\n\n"
        f"📍 Регион: {region}\n"
        f"📚 Предметы: {', '.join(subjects_names)}\n\n"
        f"Свяжитесь с нами для получения материалов:\n{CONTACT_LINK}"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ])
    await send_or_edit(query, text, markup)
    return ConversationHandler.END


# ── Оплата ──────────────────────────────────────────────────

async def pay_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    region = context.user_data.get('region', '')
    cart = context.user_data.get('cart', [])
    total, promo = calc_cart(cart)
    subjects_names = [SUBJECTS[i] for i in cart]
    context.user_data['total'] = total

    await notify_admin(
        context.bot,
        f"💳 {user_tag(user)} перешёл к оплате\n"
        f"📍 Регион: {region}\n"
        f"📚 Предметы: {', '.join(subjects_names)}\n"
        f"💰 Сумма: {total} ₽" + (" (акция)" if promo else "")
    )

    promo_note = "\n🔥 Акция: Мат + Рус + 2 предмета = 1790 ₽" if promo else ""
    text = (
        f"💳 Реквизиты для оплаты:\n\n"
        f"👤 Получатель: Артемьев Даниил Алексеевич\n"
        f"💳 Номер карты: {CARD_NUMBER}\n"
        f"💰 Сумма: {total} рублей{promo_note}\n\n"
        f"📍 Регион: {region}\n"
        f"📚 Предметы: {', '.join(subjects_names)}\n\n"
        "После оплаты пришлите скриншот чека прямо в этот чат ✅"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_cart")],
    ])
    if query.message.photo:
        await query.message.delete()
        await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=markup)
    else:
        await query.edit_message_text(text, reply_markup=markup)
    return WAIT_PAYMENT


async def back_to_cart_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    region = context.user_data.get('region', '')
    cart = context.user_data.get('cart', [])

    text = f"📍 Регион: {region}\n\n{cart_text(cart)}"
    await send_or_edit(query, text, make_cart_keyboard())
    return CONFIRM_ORDER


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    region = context.user_data.get('region', '')
    cart = context.user_data.get('cart', [])
    total = context.user_data.get('total', calc_cart(cart)[0])
    subjects_names = [SUBJECTS[i] for i in cart]
    user = update.effective_user

    await notify_admin(
        context.bot,
        f"📸 {user_tag(user)} прислал скриншот оплаты\n"
        f"📍 Регион: {region}\n"
        f"📚 Предметы: {', '.join(subjects_names)}\n"
        f"💰 Ожидаемая сумма: {total} ₽"
    )

    msg = await update.message.reply_text("⏳ Проверяю скриншот оплаты...")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()

    ok, reason = await check_payment_with_claude(bytes(image_bytes), region, subjects_names, total)

    if ok:
        add_purchase(user.id, subjects_names, total, region)
        await msg.edit_text(
            "✅ Оплата подтверждена!\n\n"
            f"📍 Регион: {region}\n"
            f"📚 Предметы: {', '.join(subjects_names)}\n\n"
            f"Свяжитесь с нами для получения материалов:\n{CONTACT_LINK}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )
        await notify_admin(
            context.bot,
            f"✅ ОПЛАТА ПОДТВЕРЖДЕНА\n"
            f"👤 {user_tag(user)}\n"
            f"📍 Регион: {region}\n"
            f"📚 Предметы: {', '.join(subjects_names)}\n"
            f"💰 Сумма: {total} ₽"
        )
        return ConversationHandler.END
    else:
        await notify_admin(
            context.bot,
            f"❌ Оплата НЕ прошла проверку\n"
            f"👤 {user_tag(user)}\nПричина: {reason}"
        )
        await msg.edit_text(
            f"❌ Не удалось подтвердить оплату.\nПричина: {reason}\n\n"
            "Проверьте:\n"
            f"• Сумма: {total} руб.\n"
            f"• Карта: {CARD_NUMBER}\n"
            f"• Статус: выполнено",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_cart")],
            ])
        )
        return WAIT_PAYMENT


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. Напишите /start чтобы начать заново.")
    return ConversationHandler.END


async def cabinet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /cabinet."""
    user = update.effective_user
    register_user(user)
    await notify_admin(context.bot, f"👤 {user_tag(user)} открыл /cabinet")

    u = get_user_data(user.id)
    ref_count = u.get("referral_count", 0)
    free = has_free_pack(user.id)
    purchases = u.get("purchases", [])
    name = u.get("first_name", "") or user.first_name or "Пользователь"
    username_line = f"@{user.username}" if user.username else f"ID: {user.id}"
    free_status = "🎁 Есть бесплатный пакет!" if free else (
        "✅ Пакет использован" if u.get("free_pack_used") else f"🔗 Прогресс: {referral_progress_bar(ref_count)}"
    )
    text = (
        f"👤 Личный кабинет\n"
        f"{'─' * 28}\n"
        f"🙋 {name}  |  {username_line}\n"
        f"{'─' * 28}\n\n"
        f"🛍 Покупок: {len(purchases)}\n"
        f"🔗 Рефералов: {ref_count}\n"
        f"{free_status}\n"
    )
    await update.message.reply_text(text, reply_markup=make_cabinet_keyboard(has_free=free))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("cabinet", cabinet_cmd),
        ],
        states={
            CAPTCHA_STATE: [
                CallbackQueryHandler(captcha_cb, pattern="^captcha:"),
            ],
            SELECT_REGION: [
                # Главное меню
                CallbackQueryHandler(menu_buy_cb, pattern="^menu_buy$"),
                CallbackQueryHandler(menu_cabinet_cb, pattern="^menu_cabinet$"),
                CallbackQueryHandler(menu_main_cb, pattern="^menu_main$"),
                # Кабинет
                CallbackQueryHandler(cab_referral_cb, pattern="^cab_referral$"),
                CallbackQueryHandler(cab_history_cb, pattern="^cab_history$"),
                CallbackQueryHandler(cab_use_free_cb, pattern="^cab_use_free$"),
                # Регионы
                CallbackQueryHandler(region_cb, pattern="^region:"),
                CallbackQueryHandler(region_page_cb, pattern="^rpage:"),
                CallbackQueryHandler(back_to_regions_cb, pattern="^back_to_regions$"),
                CallbackQueryHandler(noop_cb, pattern="^noop$"),
            ],
            SELECT_SUBJECT: [
                CallbackQueryHandler(subject_cb, pattern="^subject:"),
                CallbackQueryHandler(view_cart_cb, pattern="^view_cart$"),
                CallbackQueryHandler(clear_cart_cb, pattern="^clear_cart$"),
                CallbackQueryHandler(back_to_regions_cb, pattern="^back_to_regions$"),
                CallbackQueryHandler(region_page_cb, pattern="^rpage:"),
                CallbackQueryHandler(menu_main_cb, pattern="^menu_main$"),
                CallbackQueryHandler(noop_cb, pattern="^noop$"),
            ],
            CONFIRM_ORDER: [
                CallbackQueryHandler(pay_cb, pattern="^pay$"),
                CallbackQueryHandler(free_pack_confirm_cb, pattern="^free_pack_confirm$"),
                CallbackQueryHandler(back_to_subjects_cb, pattern="^back_to_subjects$"),
                CallbackQueryHandler(clear_cart_cb, pattern="^clear_cart$"),
                CallbackQueryHandler(menu_main_cb, pattern="^menu_main$"),
            ],
            WAIT_PAYMENT: [
                MessageHandler(filters.PHOTO, photo_handler),
                CallbackQueryHandler(back_to_cart_cb, pattern="^back_to_cart$"),
                CallbackQueryHandler(back_to_subjects_cb, pattern="^back_to_subjects$"),
                CallbackQueryHandler(menu_main_cb, pattern="^menu_main$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("cabinet", cabinet_cmd))
    app.add_error_handler(error_handler)
    logger.info("Бот запущен ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
