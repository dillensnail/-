import os
import json
import html
import logging
import random
import threading
import time as time_module
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telebot import TeleBot, types

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
ADMIN_CHAT_ID = os.environ["ADMIN_CHAT_ID"]  # чат/группа модераторов
CHANNEL_ID = os.environ["CHANNEL_ID"]        # например @moykanal или -100xxxxxxxxxx

# Настройки подписи, которая ставится в конце каждого опубликованного тейка
COMMUNITY_HANDLE = os.environ.get("COMMUNITY_HANDLE", "@Socialhostility_confa")
BOT_LINK = os.environ.get("BOT_LINK", "http://t.me/PsychoPromblembot")

# Ссылка на правила — кнопка на /start
RULES_URL = os.environ.get("RULES_URL", "https://t.me/your_rules_link")

# Настройки расписания публикаций
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
WINDOW_START_HOUR = 8    # с 8:00 мск
WINDOW_END_HOUR = 22     # до 22:00 мск
POST_GAP_MINUTES = 15    # промежуток между постами

TAGS_FILE = "hashtags.json"
SCHEDULE_FILE = "schedule.json"
CAT_ARTS_FILE = "cat_arts.json"
PHRASES_FILE = "support_phrases.json"

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")

# Как называются категории для пользователя (кнопки) и как они же пишутся тегом
CATEGORY_LABELS = {
    "расстройство": "🧠 Расстройства",
    "нейроотличие": "🧩 Нейроотличия",
}
# Разные написания, которые админ может использовать в командах /addtag /deltag
CATEGORY_ALIASES = {
    "расстройство": "расстройство",
    "расстройства": "расстройство",
    "нейроотличие": "нейроотличие",
    "нейроотличия": "нейроотличие",
}


# ---------- Хештеги (хранятся в файле, редактируются командами) ----------
def load_tags():
    if not os.path.exists(TAGS_FILE):
        default = {
            "расстройство": [
                "депрессия", "тревожноерасстройство", "паническиеатаки", "бар",
                "шар", "прл", "птср", "кптср", "окр", "рпп",
                "диссоциативныерасстройства", "шизофрения", "социофобия",
            ],
            "нейроотличие": [
                "сдвг", "рас", "дислексия", "синдромтуретта", "диспраксия",
            ],
        }
        save_tags(default)
        return default
    with open(TAGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tags(tags):
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False, indent=2)


TAGS = load_tags()


# ---------- Список арт-картинок котиков и слов поддержки (редактируются командами) ----------
def load_list_file(path, default):
    if not os.path.exists(path):
        save_list_file(path, default)
        return list(default)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_list_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


CAT_ARTS = load_list_file(CAT_ARTS_FILE, [])
SUPPORT_PHRASES = load_list_file(PHRASES_FILE, [
    "Ты не один.",
    "Спасибо, что поделился — ты значимый.",
    "Спасибо за тейк. Помни: тебе не обязательно сворачивать горы, чтобы тебя любили.",
    "Твои чувства имеют значение.",
    "Ты имеешь право на поддержку и заботу, просто потому что ты есть.",
])


# ---------- Расписание публикаций (хранится в файле, переживает перезапуски) ----------
def load_schedule():
    if not os.path.exists(SCHEDULE_FILE):
        default = {"next_slot": datetime.now(MOSCOW_TZ).isoformat(), "queue": []}
        save_schedule(default)
        return default
    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_schedule(state):
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


SCHEDULE = load_schedule()
SCHEDULE_LOCK = threading.Lock()


def clamp_to_window(dt):
    """Если время вне окна 8:00-22:00 мск — сдвигает на ближайшее начало окна."""
    local = dt.astimezone(MOSCOW_TZ)
    start = local.replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    end = local.replace(hour=WINDOW_END_HOUR, minute=0, second=0, microsecond=0)
    if local < start:
        return start
    if local >= end:
        return start + timedelta(days=1)
    return local


def schedule_add(entry_data):
    """Ставит тейк в очередь на публикацию, возвращает время публикации."""
    global SCHEDULE
    with SCHEDULE_LOCK:
        now = datetime.now(MOSCOW_TZ)
        next_slot = datetime.fromisoformat(SCHEDULE["next_slot"])
        candidate = max(now, next_slot)
        candidate = clamp_to_window(candidate)
        entry = dict(entry_data)
        entry["publish_time"] = candidate.isoformat()
        SCHEDULE["queue"].append(entry)
        SCHEDULE["next_slot"] = (candidate + timedelta(minutes=POST_GAP_MINUTES)).isoformat()
        save_schedule(SCHEDULE)
        return candidate


def send_support_gift(user_id):
    """Открытка после публикации: арт с котиком + слова поддержки (если списки не пусты)."""
    phrase = random.choice(SUPPORT_PHRASES) if SUPPORT_PHRASES else None
    art_url = random.choice(CAT_ARTS) if CAT_ARTS else None
    try:
        if art_url:
            bot.send_photo(user_id, art_url, caption=phrase)
        elif phrase:
            bot.send_message(user_id, phrase)
    except Exception:
        logging.exception("Не удалось отправить открытку поддержки")


def publish_entry(entry):
    all_tags = [entry["category"]] + entry["tags"]
    tags_line = " ".join(f"#{t}" for t in all_tags)
    signature = f'#тейк | {COMMUNITY_HANDLE} | <a href="{BOT_LINK}">takebot</a>'
    post_text = f"{html.escape(entry['text'])}\n\n{signature}\n{tags_line}"
    bot.send_message(CHANNEL_ID, post_text)
    bot.send_message(entry["user_id"], "🌟 Ваш тейк опубликован!")
    send_support_gift(entry["user_id"])


def scheduler_loop():
    global SCHEDULE
    while True:
        try:
            with SCHEDULE_LOCK:
                now = datetime.now(MOSCOW_TZ)
                changed = False
                while SCHEDULE["queue"] and datetime.fromisoformat(SCHEDULE["queue"][0]["publish_time"]) <= now:
                    entry = SCHEDULE["queue"].pop(0)
                    changed = True
                    try:
                        publish_entry(entry)
                    except Exception:
                        logging.exception("Не удалось опубликовать тейк из очереди")
                if changed:
                    save_schedule(SCHEDULE)
        except Exception:
            logging.exception("Ошибка в цикле планировщика")
        time_module.sleep(20)


# ---------- Временные данные (в памяти, пока бот запущен) ----------
user_drafts = {}          # user_id -> {"text": str, "category": str|None, "tags": set()}
pending_review = {}       # review_id -> {"user_id", "text", "category", "tags"}
review_counter = 0
review_user_id = {}          # review_id -> user_id (кто автор тейка)
admin_thread_messages = {}   # message_id в чате админов -> review_id
user_thread_messages = {}    # message_id в личке юзера -> review_id


def is_admin(user_id):
    return user_id in ADMIN_IDS


# ---------- Команды ----------
@bot.message_handler(commands=["start"], func=lambda m: m.chat.type == "private")
def cmd_start(message):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📖 Правила", url=RULES_URL))
    bot.send_message(
        message.chat.id,
        "🌸 Напишите свой тейк — поделитесь опытом или мнением.",
        reply_markup=kb,
    )


@bot.message_handler(commands=["addtag"])
def cmd_addtag(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1].lower() not in CATEGORY_ALIASES:
        bot.reply_to(message, "Использование: /addtag расстройство|нейроотличие название")
        return
    category = CATEGORY_ALIASES[parts[1].lower()]
    tag = parts[2].strip().lstrip("#").lower()
    if tag in TAGS[category]:
        bot.reply_to(message, f"Тег #{tag} уже есть в категории «{category}».")
        return
    TAGS[category].append(tag)
    save_tags(TAGS)
    bot.reply_to(message, f"Добавлен тег #{tag} в категорию «{category}»")


@bot.message_handler(commands=["deltag"])
def cmd_deltag(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1].lower() not in CATEGORY_ALIASES:
        bot.reply_to(message, "Использование: /deltag расстройство|нейроотличие название")
        return
    category = CATEGORY_ALIASES[parts[1].lower()]
    tag = parts[2].strip().lstrip("#").lower()
    if tag not in TAGS[category]:
        bot.reply_to(message, f"Тега #{tag} нет в категории «{category}».")
        return
    TAGS[category].remove(tag)
    save_tags(TAGS)
    bot.reply_to(message, f"Удалён тег #{tag} из категории «{category}»")


@bot.message_handler(commands=["taglist"])
def cmd_taglist(message):
    lines = []
    for category, tags in TAGS.items():
        lines.append(f"«{category}»: " + " ".join(f"#{t}" for t in tags))
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["queue"])
def cmd_queue(message):
    if not is_admin(message.from_user.id):
        return
    with SCHEDULE_LOCK:
        queue = SCHEDULE["queue"]
        if not queue:
            bot.reply_to(message, "Очередь пуста.")
            return
        lines = ["Очередь публикаций:"]
        for entry in queue:
            dt = datetime.fromisoformat(entry["publish_time"])
            preview = entry["text"][:40].replace("\n", " ")
            lines.append(f"• {dt.strftime('%d.%m %H:%M')} — {preview}...")
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["addcatart"])
def cmd_addcatart(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().startswith("http"):
        bot.reply_to(message, "Использование: /addcatart <прямая ссылка на картинку>")
        return
    url = parts[1].strip()
    CAT_ARTS.append(url)
    save_list_file(CAT_ARTS_FILE, CAT_ARTS)
    bot.reply_to(message, f"Добавлено. Всего картинок: {len(CAT_ARTS)}.")


@bot.message_handler(commands=["delcatart"])
def cmd_delcatart(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        bot.reply_to(message, "Использование: /delcatart номер (номера смотри в /catartlist)")
        return
    idx = int(parts[1].strip()) - 1
    if 0 <= idx < len(CAT_ARTS):
        removed = CAT_ARTS.pop(idx)
        save_list_file(CAT_ARTS_FILE, CAT_ARTS)
        bot.reply_to(message, f"Удалено: {removed}")
    else:
        bot.reply_to(message, "Нет такого номера.")


@bot.message_handler(commands=["catartlist"])
def cmd_catartlist(message):
    if not is_admin(message.from_user.id):
        return
    if not CAT_ARTS:
        bot.reply_to(message, "Список пуст. Добавь картинки: /addcatart <ссылка>")
        return
    lines = [f"{i + 1}. {u}" for i, u in enumerate(CAT_ARTS)]
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["addphrase"])
def cmd_addphrase(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Использование: /addphrase текст фразы")
        return
    SUPPORT_PHRASES.append(parts[1].strip())
    save_list_file(PHRASES_FILE, SUPPORT_PHRASES)
    bot.reply_to(message, "Фраза добавлена.")


@bot.message_handler(commands=["delphrase"])
def cmd_delphrase(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        bot.reply_to(message, "Использование: /delphrase номер (номера смотри в /phraselist)")
        return
    idx = int(parts[1].strip()) - 1
    if 0 <= idx < len(SUPPORT_PHRASES):
        removed = SUPPORT_PHRASES.pop(idx)
        save_list_file(PHRASES_FILE, SUPPORT_PHRASES)
        bot.reply_to(message, f"Удалено: {removed}")
    else:
        bot.reply_to(message, "Нет такого номера.")


@bot.message_handler(commands=["phraselist"])
def cmd_phraselist(message):
    if not is_admin(message.from_user.id):
        return
    if not SUPPORT_PHRASES:
        bot.reply_to(message, "Список пуст.")
        return
    lines = [f"{i + 1}. {p}" for i, p in enumerate(SUPPORT_PHRASES)]
    bot.reply_to(message, "\n".join(lines))


# ---------- Приём текста тейка (только личные сообщения, и только если это не ответ в диалоге) ----------
@bot.message_handler(
    func=lambda m: (
        m.text and not m.text.startswith("/") and m.chat.type == "private"
        and not (m.reply_to_message and m.reply_to_message.message_id in user_thread_messages)
    )
)
def handle_text(message):
    user_id = message.from_user.id
    user_drafts[user_id] = {"text": message.text, "category": None, "tags": set()}
    send_category_keyboard(message.chat.id)


def build_category_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(CATEGORY_LABELS["расстройство"], callback_data="cat:расстройство"),
        types.InlineKeyboardButton(CATEGORY_LABELS["нейроотличие"], callback_data="cat:нейроотличие"),
    )
    return kb


def send_category_keyboard(chat_id):
    bot.send_message(chat_id, "Выберите категорию:", reply_markup=build_category_keyboard())


def build_subtag_keyboard(user_id):
    draft = user_drafts[user_id]
    category = draft["category"]
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for tag in TAGS[category]:
        label = f"✅ #{tag}" if tag in draft["tags"] else f"#{tag}"
        buttons.append(types.InlineKeyboardButton(label, callback_data=f"tag:{tag}"))
    kb.add(*buttons)
    kb.row(types.InlineKeyboardButton("◀️ Сменить категорию", callback_data="backcat"))
    kb.row(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
        types.InlineKeyboardButton("❌ Отменить", callback_data="cancel"),
    )
    return kb


# ---------- Выбор категории ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("cat:"))
def cb_choose_category(call):
    user_id = call.from_user.id
    if user_id not in user_drafts:
        bot.answer_callback_query(call.id, "Сначала отправьте текст тейка.")
        return
    category = call.data.split(":", 1)[1]
    user_drafts[user_id]["category"] = category
    user_drafts[user_id]["tags"] = set()
    bot.edit_message_text(
        f"Категория: {CATEGORY_LABELS[category]}\nВыберите теги:",
        call.message.chat.id, call.message.message_id,
        reply_markup=build_subtag_keyboard(user_id)
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "backcat")
def cb_back_to_category(call):
    user_id = call.from_user.id
    if user_id in user_drafts:
        user_drafts[user_id]["category"] = None
        user_drafts[user_id]["tags"] = set()
    bot.edit_message_text("Выберите категорию:", call.message.chat.id, call.message.message_id,
                           reply_markup=build_category_keyboard())
    bot.answer_callback_query(call.id)


# ---------- Выбор тегов внутри категории ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("tag:"))
def cb_toggle_tag(call):
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft or not draft["category"]:
        bot.answer_callback_query(call.id, "Сначала выберите категорию.")
        return
    tag = call.data.split(":", 1)[1]
    tags = draft["tags"]
    if tag in tags:
        tags.remove(tag)
    else:
        tags.add(tag)
    bot.edit_message_reply_markup(
        call.message.chat.id, call.message.message_id,
        reply_markup=build_subtag_keyboard(user_id)
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "cancel")
def cb_cancel(call):
    user_id = call.from_user.id
    user_drafts.pop(user_id, None)
    bot.edit_message_text("Отменено.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "confirm")
def cb_confirm(call):
    global review_counter
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft or not draft["category"]:
        bot.answer_callback_query(call.id, "Черновик не найден.")
        return
    review_counter += 1
    review_id = review_counter
    pending_review[review_id] = {
        "user_id": user_id,
        "text": draft["text"],
        "category": draft["category"],
        "tags": sorted(draft["tags"]),
    }
    review_user_id[review_id] = user_id
    user_drafts.pop(user_id, None)

    bot.edit_message_text("✨ Ваш тейк отправлен на модерацию, ожидайте!",
                           call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

    item = pending_review[review_id]
    all_tags = [item["category"]] + item["tags"]
    tags_line = " ".join(f"#{t}" for t in all_tags)
    admin_text = (
        f"📝 Новый тейк на проверку (#{review_id})\n\n"
        f"{html.escape(item['text'])}\n\n"
        f"{tags_line}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{review_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{review_id}"),
    )
    admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_text, reply_markup=kb)
    admin_thread_messages[admin_msg.message_id] = review_id


# ---------- Двусторонний диалог (как в Livegram) ----------
# Админ отвечает Reply на любое сообщение из треда тейка -> уходит юзеру
@bot.message_handler(
    func=lambda m: (
        str(m.chat.id) == str(ADMIN_CHAT_ID)
        and m.reply_to_message is not None
        and m.reply_to_message.message_id in admin_thread_messages
        and m.text and not m.text.startswith("/")
    )
)
def handle_admin_reply(message):
    if not is_admin(message.from_user.id):
        return
    review_id = admin_thread_messages[message.reply_to_message.message_id]
    user_id = review_user_id.get(review_id)
    if user_id is None:
        return
    try:
        sent = bot.send_message(
            user_id,
            f"💬 Ответ от администрации по вашему тейку:\n\n{html.escape(message.text)}"
        )
        user_thread_messages[sent.message_id] = review_id
        bot.reply_to(message, "✅ Ответ отправлен пользователю.")
    except Exception:
        bot.reply_to(message, "⚠️ Не удалось отправить сообщение — возможно, пользователь заблокировал бота.")


# Юзер отвечает Reply на сообщение от администрации -> уходит в чат админов
@bot.message_handler(
    func=lambda m: (
        m.chat.type == "private"
        and m.reply_to_message is not None
        and m.reply_to_message.message_id in user_thread_messages
        and m.text and not m.text.startswith("/")
    )
)
def handle_user_reply(message):
    review_id = user_thread_messages[message.reply_to_message.message_id]
    user_name = html.escape(message.from_user.first_name or message.from_user.username or "пользователь")
    text = html.escape(message.text)
    sent = bot.send_message(
        ADMIN_CHAT_ID,
        f"↩️ Ответ от {user_name} по тейку #{review_id}:\n\n{text}"
    )
    admin_thread_messages[sent.message_id] = review_id
    bot.reply_to(message, "Сообщение отправлено администрации.")


# ---------- Модерация ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("approve:"))
def cb_approve(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = int(call.data.split(":", 1)[bot = TeleBot(BOT_TOKEN, parse_mode="HTML")

# Как называются категории для пользователя (кнопки) и как они же пишутся тегом
CATEGORY_LABELS = {
    "расстройство": "🧠 Расстройства",
    "нейроотличие": "🧩 Нейроотличия",
}
# Разные написания, которые админ может использовать в командах /addtag /deltag
CATEGORY_ALIASES = {
    "расстройство": "расстройство",
    "расстройства": "расстройство",
    "нейроотличие": "нейроотличие",
    "нейроотличия": "нейроотличие",
}


# ---------- Хештеги (хранятся в файле, редактируются командами) ----------
def load_tags():
    if not os.path.exists(TAGS_FILE):
        default = {
            "расстройство": [
                "депрессия", "тревожноерасстройство", "паническиеатаки", "бар",
                "шар", "прл", "птср", "кптср", "окр", "рпп",
                "диссоциативныерасстройства", "шизофрения", "социофобия",
            ],
            "нейроотличие": [
                "сдвг", "рас", "дислексия", "синдромтуретта", "диспраксия",
            ],
        }
        save_tags(default)
        return default
    with open(TAGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tags(tags):
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False, indent=2)


TAGS = load_tags()


# ---------- Список арт-картинок котиков и слов поддержки (редактируются командами) ----------
def load_list_file(path, default):
    if not os.path.exists(path):
        save_list_file(path, default)
        return list(default)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_list_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


CAT_ARTS = load_list_file(CAT_ARTS_FILE, [])
SUPPORT_PHRASES = load_list_file(PHRASES_FILE, [
    "Ты не один.",
    "Спасибо, что поделился — ты значимый.",
    "Спасибо за тейк. Помни: тебе не обязательно сворачивать горы, чтобы тебя любили.",
    "Твои чувства имеют значение.",
    "Ты имеешь право на поддержку и заботу, просто потому что ты есть.",
])


# ---------- Расписание публикаций (хранится в файле, переживает перезапуски) ----------
def load_schedule():
    if not os.path.exists(SCHEDULE_FILE):
        default = {"next_slot": datetime.now(MOSCOW_TZ).isoformat(), "queue": []}
        save_schedule(default)
        return default
    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_schedule(state):
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


SCHEDULE = load_schedule()
SCHEDULE_LOCK = threading.Lock()


def clamp_to_window(dt):
    """Если время вне окна 8:00-22:00 мск — сдвигает на ближайшее начало окна."""
    local = dt.astimezone(MOSCOW_TZ)
    start = local.replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    end = local.replace(hour=WINDOW_END_HOUR, minute=0, second=0, microsecond=0)
    if local < start:
        return start
    if local >= end:
        return start + timedelta(days=1)
    return local


def schedule_add(entry_data):
    """Ставит тейк в очередь на публикацию, возвращает время публикации."""
    global SCHEDULE
    with SCHEDULE_LOCK:
        now = datetime.now(MOSCOW_TZ)
        next_slot = datetime.fromisoformat(SCHEDULE["next_slot"])
        candidate = max(now, next_slot)
        candidate = clamp_to_window(candidate)
        entry = dict(entry_data)
        entry["publish_time"] = candidate.isoformat()
        SCHEDULE["queue"].append(entry)
        SCHEDULE["next_slot"] = (candidate + timedelta(minutes=POST_GAP_MINUTES)).isoformat()
        save_schedule(SCHEDULE)
        return candidate


def send_support_gift(user_id):
    """Открытка после публикации: арт с котиком + слова поддержки (если списки не пусты)."""
    phrase = random.choice(SUPPORT_PHRASES) if SUPPORT_PHRASES else None
    art_url = random.choice(CAT_ARTS) if CAT_ARTS else None
    try:
        if art_url:
            bot.send_photo(user_id, art_url, caption=phrase)
        elif phrase:
            bot.send_message(user_id, phrase)
    except Exception:
        logging.exception("Не удалось отправить открытку поддержки")


def publish_entry(entry):
    all_tags = [entry["category"]] + entry["tags"]
    tags_line = " ".join(f"#{t}" for t in all_tags)
    signature = f'#тейк | {COMMUNITY_HANDLE} | <a href="{BOT_LINK}">takebot</a>'
    post_text = f"{html.escape(entry['text'])}\n\n{signature}\n{tags_line}"
    bot.send_message(CHANNEL_ID, post_text)
    bot.send_message(entry["user_id"], "🌟 Ваш тейк опубликован!")
    send_support_gift(entry["user_id"])


def scheduler_loop():
    global SCHEDULE
    while True:
        try:
            with SCHEDULE_LOCK:
                now = datetime.now(MOSCOW_TZ)
                changed = False
                while SCHEDULE["queue"] and datetime.fromisoformat(SCHEDULE["queue"][0]["publish_time"]) <= now:
                    entry = SCHEDULE["queue"].pop(0)
                    changed = True
                    try:
                        publish_entry(entry)
                    except Exception:
                        logging.exception("Не удалось опубликовать тейк из очереди")
                if changed:
                    save_schedule(SCHEDULE)
        except Exception:
            logging.exception("Ошибка в цикле планировщика")
        time_module.sleep(20)


# ---------- Временные данные (в памяти, пока бот запущен) ----------
user_drafts = {}          # user_id -> {"text": str, "category": str|None, "tags": set()}
pending_review = {}       # review_id -> {"user_id", "text", "category", "tags"}
review_counter = 0
review_user_id = {}          # review_id -> user_id (кто автор тейка)
admin_thread_messages = {}   # message_id в чате админов -> review_id
user_thread_messages = {}    # message_id в личке юзера -> review_id


def is_admin(user_id):
    return user_id in ADMIN_IDS


# ---------- Команды ----------
@bot.message_handler(commands=["start"], func=lambda m: m.chat.type == "private")
def cmd_start(message):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📖 Правила", url=RULES_URL))
    bot.send_message(
        message.chat.id,
        "🌸 Напишите свой тейк — поделитесь опытом или мнением.",
        reply_markup=kb,
    )


@bot.message_handler(commands=["addtag"])
def cmd_addtag(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1].lower() not in CATEGORY_ALIASES:
        bot.reply_to(message, "Использование: /addtag расстройство|нейроотличие название")
        return
    category = CATEGORY_ALIASES[parts[1].lower()]
    tag = parts[2].strip().lstrip("#").lower()
    if tag in TAGS[category]:
        bot.reply_to(message, f"Тег #{tag} уже есть в категории «{category}».")
        return
    TAGS[category].append(tag)
    save_tags(TAGS)
    bot.reply_to(message, f"Добавлен тег #{tag} в категорию «{category}»")


@bot.message_handler(commands=["deltag"])
def cmd_deltag(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1].lower() not in CATEGORY_ALIASES:
        bot.reply_to(message, "Использование: /deltag расстройство|нейроотличие название")
        return
    category = CATEGORY_ALIASES[parts[1].lower()]
    tag = parts[2].strip().lstrip("#").lower()
    if tag not in TAGS[category]:
        bot.reply_to(message, f"Тега #{tag} нет в категории «{category}».")
        return
    TAGS[category].remove(tag)
    save_tags(TAGS)
    bot.reply_to(message, f"Удалён тег #{tag} из категории «{category}»")


@bot.message_handler(commands=["taglist"])
def cmd_taglist(message):
    lines = []
    for category, tags in TAGS.items():
        lines.append(f"«{category}»: " + " ".join(f"#{t}" for t in tags))
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["queue"])
def cmd_queue(message):
    if not is_admin(message.from_user.id):
        return
    with SCHEDULE_LOCK:
        queue = SCHEDULE["queue"]
        if not queue:
            bot.reply_to(message, "Очередь пуста.")
            return
        lines = ["Очередь публикаций:"]
        for entry in queue:
            dt = datetime.fromisoformat(entry["publish_time"])
            preview = entry["text"][:40].replace("\n", " ")
            lines.append(f"• {dt.strftime('%d.%m %H:%M')} — {preview}...")
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["addcatart"])
def cmd_addcatart(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().startswith("http"):
        bot.reply_to(message, "Использование: /addcatart <прямая ссылка на картинку>")
        return
    url = parts[1].strip()
    CAT_ARTS.append(url)
    save_list_file(CAT_ARTS_FILE, CAT_ARTS)
    bot.reply_to(message, f"Добавлено. Всего картинок: {len(CAT_ARTS)}.")


@bot.message_handler(commands=["delcatart"])
def cmd_delcatart(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        bot.reply_to(message, "Использование: /delcatart номер (номера смотри в /catartlist)")
        return
    idx = int(parts[1].strip()) - 1
    if 0 <= idx < len(CAT_ARTS):
        removed = CAT_ARTS.pop(idx)
        save_list_file(CAT_ARTS_FILE, CAT_ARTS)
        bot.reply_to(message, f"Удалено: {removed}")
    else:
        bot.reply_to(message, "Нет такого номера.")


@bot.message_handler(commands=["catartlist"])
def cmd_catartlist(message):
    if not is_admin(message.from_user.id):
        return
    if not CAT_ARTS:
        bot.reply_to(message, "Список пуст. Добавь картинки: /addcatart <ссылка>")
        return
    lines = [f"{i + 1}. {u}" for i, u in enumerate(CAT_ARTS)]
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["addphrase"])
def cmd_addphrase(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Использование: /addphrase текст фразы")
        return
    SUPPORT_PHRASES.append(parts[1].strip())
    save_list_file(PHRASES_FILE, SUPPORT_PHRASES)
    bot.reply_to(message, "Фраза добавлена.")


@bot.message_handler(commands=["delphrase"])
def cmd_delphrase(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        bot.reply_to(message, "Использование: /delphrase номер (номера смотри в /phraselist)")
        return
    idx = int(parts[1].strip()) - 1
    if 0 <= idx < len(SUPPORT_PHRASES):
        removed = SUPPORT_PHRASES.pop(idx)
        save_list_file(PHRASES_FILE, SUPPORT_PHRASES)
        bot.reply_to(message, f"Удалено: {removed}")
    else:
        bot.reply_to(message, "Нет такого номера.")


@bot.message_handler(commands=["phraselist"])
def cmd_phraselist(message):
    if not is_admin(message.from_user.id):
        return
    if not SUPPORT_PHRASES:
        bot.reply_to(message, "Список пуст.")
        return
    lines = [f"{i + 1}. {p}" for i, p in enumerate(SUPPORT_PHRASES)]
    bot.reply_to(message, "\n".join(lines))


# ---------- Приём текста тейка (только личные сообщения, и только если это не ответ в диалоге) ----------
@bot.message_handler(
    func=lambda m: (
        m.text and not m.text.startswith("/") and m.chat.type == "private"
        and not (m.reply_to_message and m.reply_to_message.message_id in user_thread_messages)
    )
)
def handle_text(message):
    user_id = message.from_user.id
    user_drafts[user_id] = {"text": message.text, "category": None, "tags": set()}
    send_category_keyboard(message.chat.id)


def build_category_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(CATEGORY_LABELS["расстройство"], callback_data="cat:расстройство"),
        types.InlineKeyboardButton(CATEGORY_LABELS["нейроотличие"], callback_data="cat:нейроотличие"),
    )
    return kb


def send_category_keyboard(chat_id):
    bot.send_message(chat_id, "Выберите категорию:", reply_markup=build_category_keyboard())


def build_subtag_keyboard(user_id):
    draft = user_drafts[user_id]
    category = draft["category"]
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for tag in TAGS[category]:
        label = f"✅ #{tag}" if tag in draft["tags"] else f"#{tag}"
        buttons.append(types.InlineKeyboardButton(label, callback_data=f"tag:{tag}"))
    kb.add(*buttons)
    kb.row(types.InlineKeyboardButton("◀️ Сменить категорию", callback_data="backcat"))
    kb.row(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
        types.InlineKeyboardButton("❌ Отменить", callback_data="cancel"),
    )
    return kb


# ---------- Выбор категории ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("cat:"))
def cb_choose_category(call):
    user_id = call.from_user.id
    if user_id not in user_drafts:
        bot.answer_callback_query(call.id, "Сначала отправьте текст тейка.")
        return
    category = call.data.split(":", 1)[1]
    user_drafts[user_id]["category"] = category
    user_drafts[user_id]["tags"] = set()
    bot.edit_message_text(
        f"Категория: {CATEGORY_LABELS[category]}\nВыберите теги:",
        call.message.chat.id, call.message.message_id,
        reply_markup=build_subtag_keyboard(user_id)
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "backcat")
def cb_back_to_category(call):
    user_id = call.from_user.id
    if user_id in user_drafts:
        user_drafts[user_id]["category"] = None
        user_drafts[user_id]["tags"] = set()
    bot.edit_message_text("Выберите категорию:", call.message.chat.id, call.message.message_id,
                           reply_markup=build_category_keyboard())
    bot.answer_callback_query(call.id)


# ---------- Выбор тегов внутри категории ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("tag:"))
def cb_toggle_tag(call):
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft or not draft["category"]:
        bot.answer_callback_query(call.id, "Сначала выберите категорию.")
        return
    tag = call.data.split(":", 1)[1]
    tags = draft["tags"]
    if tag in tags:
        tags.remove(tag)
    else:
        tags.add(tag)
    bot.edit_message_reply_markup(
        call.message.chat.id, call.message.message_id,
        reply_markup=build_subtag_keyboard(user_id)
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "cancel")
def cb_cancel(call):
    user_id = call.from_user.id
    user_drafts.pop(user_id, None)
    bot.edit_message_text("Отменено.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "confirm")
def cb_confirm(call):
    global review_counter
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft or not draft["category"]:
        bot.answer_callback_query(call.id, "Черновик не найден.")
        return
    review_counter += 1
    review_id = review_counter
    pending_review[review_id] = {
        "user_id": user_id,
        "text": draft["text"],
        "category": draft["category"],
        "tags": sorted(draft["tags"]),
    }
    review_user_id[review_id] = user_id
    user_drafts.pop(user_id, None)

    bot.edit_message_text("✨ Ваш тейк отправлен на модерацию, ожидайте!",
                           call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

    item = pending_review[review_id]
    all_tags = [item["category"]] + item["tags"]
    tags_line = " ".join(f"#{t}" for t in all_tags)
    admin_text = (
        f"📝 Новый тейк на проверку (#{review_id})\n\n"
        f"{html.escape(item['text'])}\n\n"
        f"{tags_line}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{review_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{review_id}"),
    )
    admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_text, reply_markup=kb)
    admin_thread_messages[admin_msg.message_id] = review_id


# ---------- Двусторонний диалог (как в Livegram) ----------
# Админ отвечает Reply на любое сообщение из треда тейка -> уходит юзеру
@bot.message_handler(
    func=lambda m: (
        str(m.chat.id) == str(ADMIN_CHAT_ID)
        and m.reply_to_message is not None
        and m.reply_to_message.message_id in admin_thread_messages
        and m.text and not m.text.startswith("/")
    )
)
def handle_admin_reply(message):
    if not is_admin(message.from_user.id):
        return
    review_id = admin_thread_messages[message.reply_to_message.message_id]
    user_id = review_user_id.get(review_id)
    if user_id is None:
        return
    try:
        sent = bot.send_message(
            user_id,
            f"💬 Ответ от администрации по вашему тейку:\n\n{html.escape(message.text)}"
        )
        user_thread_messages[sent.message_id] = review_id
        bot.reply_to(message, "✅ Ответ отправлен пользователю.")
    except Exception:
        bot.reply_to(message, "⚠️ Не удалось отправить сообщение — возможно, пользователь заблокировал бота.")


# Юзер отвечает Reply на сообщение от администрации -> уходит в чат админов
@bot.message_handler(
    func=lambda m: (
        m.chat.type == "private"
        and m.reply_to_message is not None
        and m.reply_to_message.message_id in user_thread_messages
        and m.text and not m.text.startswith("/")
    )
)
def handle_user_reply(message):
    review_id = user_thread_messages[message.reply_to_message.message_id]
    user_name = html.escape(message.from_user.first_name or message.from_user.username or "пользователь")
    text = html.escape(message.text)
    sent = bot.send_message(
        ADMIN_CHAT_ID,
        f"↩️ Ответ от {user_name} по тейку #{review_id}:\n\n{text}"
    )
    admin_thread_messages[sent.message_id] = review_id
    bot.reply_to(message, "Сообщение отправлено администрации.")


# ---------- Модерация ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("approve:"))
def cb_approve(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = int(call.data.split(":", 1)[}
# Разные написания, которые админ может использовать в командах /addtag /deltag
CATEGORY_ALIASES = {
    "расстройство": "расстройство",
    "расстройства": "расстройство",
    "нейроотличие": "нейроотличие",
    "нейроотличия": "нейроотличие",
}


# ---------- Хештеги (хранятся в файле, редактируются командами) ----------
def load_tags():
    if not os.path.exists(TAGS_FILE):
        default = {
            "расстройство": [
                "депрессия", "тревожноерасстройство", "паническиеатаки", "бар",
                "шар", "прл", "птср", "кптср", "окр", "рпп",
                "диссоциативныерасстройства", "шизофрения", "социофобия",
            ],
            "нейроотличие": [
                "сдвг", "рас", "дислексия", "синдромтуретта", "диспраксия",
            ],
        }
        save_tags(default)
        return default
    with open(TAGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tags(tags):
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False, indent=2)


TAGS = load_tags()


# ---------- Расписание публикаций (хранится в файле, переживает перезапуски) ----------
def load_schedule():
    if not os.path.exists(SCHEDULE_FILE):
        default = {"next_slot": datetime.now(MOSCOW_TZ).isoformat(), "queue": []}
        save_schedule(default)
        return default
    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_schedule(state):
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


SCHEDULE = load_schedule()
SCHEDULE_LOCK = threading.Lock()


def clamp_to_window(dt):
    """Если время вне окна 8:00-22:00 мск — сдвигает на ближайшее начало окна."""
    local = dt.astimezone(MOSCOW_TZ)
    start = local.replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    end = local.replace(hour=WINDOW_END_HOUR, minute=0, second=0, microsecond=0)
    if local < start:
        return start
    if local >= end:
        return start + timedelta(days=1)
    return local


def schedule_add(entry_data):
    """Ставит тейк в очередь на публикацию, возвращает время публикации."""
    global SCHEDULE
    with SCHEDULE_LOCK:
        now = datetime.now(MOSCOW_TZ)
        next_slot = datetime.fromisoformat(SCHEDULE["next_slot"])
        candidate = max(now, next_slot)
        candidate = clamp_to_window(candidate)
        entry = dict(entry_data)
        entry["publish_time"] = candidate.isoformat()
        SCHEDULE["queue"].append(entry)
        SCHEDULE["next_slot"] = (candidate + timedelta(minutes=POST_GAP_MINUTES)).isoformat()
        save_schedule(SCHEDULE)
        return candidate


def publish_entry(entry):
    all_tags = [entry["category"]] + entry["tags"]
    tags_line = " ".join(f"#{t}" for t in all_tags)
    signature = f'#тейк | {COMMUNITY_HANDLE} | <a href="{BOT_LINK}">takebot</a>'
    post_text = f"{html.escape(entry['text'])}\n\n{signature}\n{tags_line}"
    bot.send_message(CHANNEL_ID, post_text)
    bot.send_message(entry["user_id"], "🌟 Ваш тейк опубликован!")


def scheduler_loop():
    global SCHEDULE
    while True:
        try:
            with SCHEDULE_LOCK:
                now = datetime.now(MOSCOW_TZ)
                changed = False
                while SCHEDULE["queue"] and datetime.fromisoformat(SCHEDULE["queue"][0]["publish_time"]) <= now:
                    entry = SCHEDULE["queue"].pop(0)
                    changed = True
                    try:
                        publish_entry(entry)
                    except Exception:
                        logging.exception("Не удалось опубликовать тейк из очереди")
                if changed:
                    save_schedule(SCHEDULE)
        except Exception:
            logging.exception("Ошибка в цикле планировщика")
        time_module.sleep(20)


# ---------- Временные данные (в памяти, пока бот запущен) ----------
user_drafts = {}      # user_id -> {"text": str, "category": str|None, "tags": set()}
pending_review = {}   # review_id -> {"user_id", "text", "category", "tags"}
review_counter = 0
review_messages = {}  # message_id (в чате админов) -> {"review_id", "user_id"} — для ответов админов


def is_admin(user_id):
    return user_id in ADMIN_IDS


# ---------- Команды ----------
@bot.message_handler(commands=["start"], func=lambda m: m.chat.type == "private")
def cmd_start(message):
    bot.send_message(message.chat.id, "🌸 Напишите свой тейк — поделитесь опытом или мнением.")


@bot.message_handler(commands=["addtag"])
def cmd_addtag(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1].lower() not in CATEGORY_ALIASES:
        bot.reply_to(message, "Использование: /addtag расстройство|нейроотличие название")
        return
    category = CATEGORY_ALIASES[parts[1].lower()]
    tag = parts[2].strip().lstrip("#").lower()
    if tag in TAGS[category]:
        bot.reply_to(message, f"Тег #{tag} уже есть в категории «{category}».")
        return
    TAGS[category].append(tag)
    save_tags(TAGS)
    bot.reply_to(message, f"Добавлен тег #{tag} в категорию «{category}»")


@bot.message_handler(commands=["deltag"])
def cmd_deltag(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1].lower() not in CATEGORY_ALIASES:
        bot.reply_to(message, "Использование: /deltag расстройство|нейроотличие название")
        return
    category = CATEGORY_ALIASES[parts[1].lower()]
    tag = parts[2].strip().lstrip("#").lower()
    if tag not in TAGS[category]:
        bot.reply_to(message, f"Тега #{tag} нет в категории «{category}».")
        return
    TAGS[category].remove(tag)
    save_tags(TAGS)
    bot.reply_to(message, f"Удалён тег #{tag} из категории «{category}»")


@bot.message_handler(commands=["taglist"])
def cmd_taglist(message):
    lines = []
    for category, tags in TAGS.items():
        lines.append(f"«{category}»: " + " ".join(f"#{t}" for t in tags))
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["queue"])
def cmd_queue(message):
    if not is_admin(message.from_user.id):
        return
    with SCHEDULE_LOCK:
        queue = SCHEDULE["queue"]
        if not queue:
            bot.reply_to(message, "Очередь пуста.")
            return
        lines = ["Очередь публикаций:"]
        for entry in queue:
            dt = datetime.fromisoformat(entry["publish_time"])
            preview = entry["text"][:40].replace("\n", " ")
            lines.append(f"• {dt.strftime('%d.%m %H:%M')} — {preview}...")
    bot.reply_to(message, "\n".join(lines))


# ---------- Приём текста тейка (только личные сообщения) ----------
@bot.message_handler(
    func=lambda m: m.text and not m.text.startswith("/") and m.chat.type == "private"
)
def handle_text(message):
    user_id = message.from_user.id
    user_drafts[user_id] = {"text": message.text, "category": None, "tags": set()}
    send_category_keyboard(message.chat.id)


def build_category_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(CATEGORY_LABELS["расстройство"], callback_data="cat:расстройство"),
        types.InlineKeyboardButton(CATEGORY_LABELS["нейроотличие"], callback_data="cat:нейроотличие"),
    )
    return kb


def send_category_keyboard(chat_id):
    bot.send_message(chat_id, "Выберите категорию:", reply_markup=build_category_keyboard())


def build_subtag_keyboard(user_id):
    draft = user_drafts[user_id]
    category = draft["category"]
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for tag in TAGS[category]:
        label = f"✅ #{tag}" if tag in draft["tags"] else f"#{tag}"
        buttons.append(types.InlineKeyboardButton(label, callback_data=f"tag:{tag}"))
    kb.add(*buttons)
    kb.row(types.InlineKeyboardButton("◀️ Сменить категорию", callback_data="backcat"))
    kb.row(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
        types.InlineKeyboardButton("❌ Отменить", callback_data="cancel"),
    )
    return kb


# ---------- Выбор категории ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("cat:"))
def cb_choose_category(call):
    user_id = call.from_user.id
    if user_id not in user_drafts:
        bot.answer_callback_query(call.id, "Сначала отправьте текст тейка.")
        return
    category = call.data.split(":", 1)[1]
    user_drafts[user_id]["category"] = category
    user_drafts[user_id]["tags"] = set()
    bot.edit_message_text(
        f"Категория: {CATEGORY_LABELS[category]}\nВыберите теги:",
        call.message.chat.id, call.message.message_id,
        reply_markup=build_subtag_keyboard(user_id)
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "backcat")
def cb_back_to_category(call):
    user_id = call.from_user.id
    if user_id in user_drafts:
        user_drafts[user_id]["category"] = None
        user_drafts[user_id]["tags"] = set()
    bot.edit_message_text("Выберите категорию:", call.message.chat.id, call.message.message_id,
                           reply_markup=build_category_keyboard())
    bot.answer_callback_query(call.id)


# ---------- Выбор тегов внутри категории ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("tag:"))
def cb_toggle_tag(call):
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft or not draft["category"]:
        bot.answer_callback_query(call.id, "Сначала выберите категорию.")
        return
    tag = call.data.split(":", 1)[1]
    tags = draft["tags"]
    if tag in tags:
        tags.remove(tag)
    else:
        tags.add(tag)
    bot.edit_message_reply_markup(
        call.message.chat.id, call.message.message_id,
        reply_markup=build_subtag_keyboard(user_id)
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "cancel")
def cb_cancel(call):
    user_id = call.from_user.id
    user_drafts.pop(user_id, None)
    bot.edit_message_text("Отменено.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "confirm")
def cb_confirm(call):
    global review_counter
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft or not draft["category"]:
        bot.answer_callback_query(call.id, "Черновик не найден.")
        return
    review_counter += 1
    review_id = review_counter
    pending_review[review_id] = {
        "user_id": user_id,
        "text": draft["text"],
        "category": draft["category"],
        "tags": sorted(draft["tags"]),
    }
    user_drafts.pop(user_id, None)

    bot.edit_message_text("✨ Ваш тейк отправлен на модерацию, ожидайте!",
                           call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

    item = pending_review[review_id]
    all_tags = [item["category"]] + item["tags"]
    tags_line = " ".join(f"#{t}" for t in all_tags)
    admin_text = (
        f"📝 Новый тейк на проверку (#{review_id})\n\n"
        f"{html.escape(item['text'])}\n\n"
        f"{tags_line}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{review_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{review_id}"),
    )
    admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_text, reply_markup=kb)
    review_messages[admin_msg.message_id] = {"review_id": review_id, "user_id": item["user_id"]}


# ---------- Ответ админа юзеру (в стиле Livegram — через Reply на сообщение о тейке) ----------
@bot.message_handler(
    func=lambda m: str(m.chat.id) == str(ADMIN_CHAT_ID)
    and m.reply_to_message is not None
    and m.text
    and not m.text.startswith("/")
)
def handle_admin_reply(message):
    if not is_admin(message.from_user.id):
        return
    target = review_messages.get(message.reply_to_message.message_id)
    if not target:
        return  # ответили не на сообщение о тейке — игнорируем
    user_id = target["user_id"]
    try:
        bot.send_message(user_id, f"💬 Ответ от администрации по вашему тейку:\n\n{html.escape(message.text)}")
        bot.reply_to(message, "✅ Ответ отправлен пользователю.")
    except Exception:
        bot.reply_to(message, "⚠️ Не удалось отправить сообщение — возможно, пользователь заблокировал бота.")


# ---------- Модерация ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("approve:"))
def cb_approve(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = int(call.data.split(":", 1)[1])
    item = pending_review.pop(review_id, None)
    if not item:
        bot.answer_callback_query(call.id, "Уже обработано.")
        return

    publish_time = schedule_add(item)
    time_str = publish_time.strftime("%d.%m %H:%M")
    admin_name = html.escape(call.from_user.first_name or call.from_user.username or "админ")

    bot.send_message(item["user_id"], f"✅ Ваш тейк одобрен! Публикация запланирована на {time_str} (мск).")
    bot.edit_message_text(f"✅ Принято ({admin_name}), запланировано на {time_str} (мск).",
                           call.message.chat.id, call.message.message_id,
                           reply_markup=types.InlineKeyboardMarkup())
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("reject:"))
def cb_reject(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = int(call.data.split(":", 1)[1])
    item = pending_review.pop(review_id, None)
    if not item:
        bot.answer_callback_query(call.id, "Уже обработано.")
        return
    admin_name = html.escape(call.from_user.first_name or call.from_user.username or "админ")
    bot.send_message(item["user_id"], "Ваш тейк отклонён модератором.")
    bot.edit_message_text(f"❌ Отклонено ({admin_name}).", call.message.chat.id, call.message.message_id,
                           reply_markup=types.InlineKeyboardMarkup())
    bot.answer_callback_query(call.id)


if __name__ == "__main__":
    print("Bot started")
    threading.Thread(target=scheduler_loop, daemon=True).start()
    bot.infinity_polling()
def load_tags():
    if not os.path.exists(TAGS_FILE):
        default = {
            "расстройство": [
                "депрессия", "тревожноерасстройство", "паническиеатаки", "бар",
                "шар", "прл", "птср", "кптср", "окр", "рпп",
                "диссоциативныерасстройства", "шизофрения", "социофобия",
            ],
            "нейроотличие": [
                "сдвг", "рас", "дислексия", "синдромтуретта", "диспраксия",
            ],
        }
        save_tags(default)
        return default
    with open(TAGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tags(tags):
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False, indent=2)


TAGS = load_tags()

# ---------- Временные данные (в памяти, пока бот запущен) ----------
user_drafts = {}      # user_id -> {"text": str, "category": str|None, "tags": set()}
pending_review = {}   # review_id -> {"user_id", "text", "category", "tags"}
review_counter = 0


def is_admin(user_id):
    return user_id in ADMIN_IDS


# ---------- Команды ----------
@bot.message_handler(commands=["start"], func=lambda m: m.chat.type == "private")
def cmd_start(message):
    bot.send_message(message.chat.id, "🌸 Напишите свой тейк,а мы его опубликуем. Бот сам ставит оформление,включая хештеги")


@bot.message_handler(commands=["addtag"])
def cmd_addtag(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1].lower() not in CATEGORY_ALIASES:
        bot.reply_to(message, "Использование: /addtag расстройство|нейроотличие название")
        return
    category = CATEGORY_ALIASES[parts[1].lower()]
    tag = parts[2].strip().lstrip("#").lower()
    if tag in TAGS[category]:
        bot.reply_to(message, f"Тег #{tag} уже есть в категории «{category}».")
        return
    TAGS[category].append(tag)
    save_tags(TAGS)
    bot.reply_to(message, f"Добавлен тег #{tag} в категорию «{category}»")


@bot.message_handler(commands=["deltag"])
def cmd_deltag(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1].lower() not in CATEGORY_ALIASES:
        bot.reply_to(message, "Использование: /deltag расстройство|нейроотличие название")
        return
    category = CATEGORY_ALIASES[parts[1].lower()]
    tag = parts[2].strip().lstrip("#").lower()
    if tag not in TAGS[category]:
        bot.reply_to(message, f"Тега #{tag} нет в категории «{category}».")
        return
    TAGS[category].remove(tag)
    save_tags(TAGS)
    bot.reply_to(message, f"Удалён тег #{tag} из категории «{category}»")


@bot.message_handler(commands=["taglist"])
def cmd_taglist(message):
    lines = []
    for category, tags in TAGS.items():
        lines.append(f"«{category}»: " + " ".join(f"#{t}" for t in tags))
    bot.reply_to(message, "\n".join(lines))


# ---------- Приём текста тейка ----------
@bot.message_handler(
    func=lambda m: m.text and not m.text.startswith("/") and m.chat.type == "private"
)
def handle_text(message):
    user_id = message.from_user.id
    user_drafts[user_id] = {"text": message.text, "category": None, "tags": set()}
    send_category_keyboard(message.chat.id)


def build_category_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(CATEGORY_LABELS["расстройство"], callback_data="cat:расстройство"),
        types.InlineKeyboardButton(CATEGORY_LABELS["нейроотличие"], callback_data="cat:нейроотличие"),
    )
    return kb


def send_category_keyboard(chat_id):
    bot.send_message(chat_id, "Выберите категорию:", reply_markup=build_category_keyboard())


def build_subtag_keyboard(user_id):
    draft = user_drafts[user_id]
    category = draft["category"]
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for tag in TAGS[category]:
        label = f"✅ #{tag}" if tag in draft["tags"] else f"#{tag}"
        buttons.append(types.InlineKeyboardButton(label, callback_data=f"tag:{tag}"))
    kb.add(*buttons)
    kb.row(types.InlineKeyboardButton("◀️ Сменить категорию", callback_data="backcat"))
    kb.row(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
        types.InlineKeyboardButton("❌ Отменить", callback_data="cancel"),
    )
    return kb


# ---------- Выбор категории ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("cat:"))
def cb_choose_category(call):
    user_id = call.from_user.id
    if user_id not in user_drafts:
        bot.answer_callback_query(call.id, "Сначала отправьте текст тейка.")
        return
    category = call.data.split(":", 1)[1]
    user_drafts[user_id]["category"] = category
    user_drafts[user_id]["tags"] = set()
    bot.edit_message_text(
        f"Категория: {CATEGORY_LABELS[category]}\nВыберите теги:",
        call.message.chat.id, call.message.message_id,
        reply_markup=build_subtag_keyboard(user_id)
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "backcat")
def cb_back_to_category(call):
    user_id = call.from_user.id
    if user_id in user_drafts:
        user_drafts[user_id]["category"] = None
        user_drafts[user_id]["tags"] = set()
    bot.edit_message_text("Выберите категорию:", call.message.chat.id, call.message.message_id,
                           reply_markup=build_category_keyboard())
    bot.answer_callback_query(call.id)


# ---------- Выбор тегов внутри категории ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("tag:"))
def cb_toggle_tag(call):
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft or not draft["category"]:
        bot.answer_callback_query(call.id, "Сначала выберите категорию.")
        return
    tag = call.data.split(":", 1)[1]
    tags = draft["tags"]
    if tag in tags:
        tags.remove(tag)
    else:
        tags.add(tag)
    bot.edit_message_reply_markup(
        call.message.chat.id, call.message.message_id,
        reply_markup=build_subtag_keyboard(user_id)
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "cancel")
def cb_cancel(call):
    user_id = call.from_user.id
    user_drafts.pop(user_id, None)
    bot.edit_message_text("Отменено.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "confirm")
def cb_confirm(call):
    global review_counter
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft or not draft["category"]:
        bot.answer_callback_query(call.id, "Черновик не найден.")
        return
    review_counter += 1
    review_id = review_counter
    pending_review[review_id] = {
        "user_id": user_id,
        "text": draft["text"],
        "category": draft["category"],
        "tags": sorted(draft["tags"]),
    }
    user_drafts.pop(user_id, None)

    bot.edit_message_text("✨ Ваш тейк отправлен на модерацию, ожидайте!",
                           call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

    item = pending_review[review_id]
    all_tags = [item["category"]] + item["tags"]
    tags_line = " ".join(f"#{t}" for t in all_tags)
    admin_text = (
        f"📝 Новый тейк на проверку (#{review_id})\n\n"
        f"{html.escape(item['text'])}\n\n"
        f"{tags_line}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{review_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{review_id}"),
    )
    bot.send_message(ADMIN_CHAT_ID, admin_text, reply_markup=kb)


# ---------- Модерация ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("approve:"))
def cb_approve(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = int(call.data.split(":", 1)[1])
    item = pending_review.pop(review_id, None)
    if not item:
        bot.answer_callback_query(call.id, "Уже обработано.")
        return

    all_tags = [item["category"]] + item["tags"]
    tags_line = " ".join(f"#{t}" for t in all_tags)
    signature = f'#тейк | {COMMUNITY_HANDLE} | <a href="{BOT_LINK}">takebot</a>'
    post_text = f"{html.escape(item['text'])}\n\n{signature}\n{tags_line}"

    bot.send_message(CHANNEL_ID, post_text)
    bot.send_message(item["user_id"], "🌟 Ваш тейк опубликован!")
    bot.edit_message_text("✅ Одобрено и опубликовано.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("reject:"))
def cb_reject(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = int(call.data.split(":", 1)[1])
    item = pending_review.pop(review_id, None)
    if not item:
        bot.answer_callback_query(call.id, "Уже обработано.")
        return
    bot.send_message(item["user_id"], "Ваш тейк отклонён модератором.")
    bot.edit_message_text("❌ Отклонено.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


if __name__ == "__main__":
    print("Bot started")
    bot.infinity_polling()

# ---------- Модерация ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("approve:"))
def cb_approve(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = int(call.data.split(":", 1)[1])
    item = pending_review.pop(review_id, None)
    if not item:
        bot.answer_callback_query(call.id, "Уже обработано.")
        return

    all_tags = [item["category"]] + item["tags"]
    tags_line = " ".join(f"#{t}" for t in all_tags)
    signature = f'#тейк | {COMMUNITY_HANDLE} | <a href="{BOT_LINK}">takebot</a>'
    post_text = f"{html.escape(item['text'])}\n\n{signature}\n{tags_line}"

    bot.send_message(CHANNEL_ID, post_text)
    bot.send_message(item["user_id"], "🌟 Ваш тейк опубликован!")
    bot.edit_message_text("✅ Одобрено и опубликовано.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("reject:"))
def cb_reject(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = int(call.data.split(":", 1)[1])
    item = pending_review.pop(review_id, None)
    if not item:
        bot.answer_callback_query(call.id, "Уже обработано.")
        return
    bot.send_message(item["user_id"], "Ваш тейк отклонён модератором.")
    bot.edit_message_text("❌ Отклонено.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)


if __name__ == "__main__":
    print("Bot started")
    bot.infinity_polling()
