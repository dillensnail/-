import os
import json
import html
import logging
from telebot import TeleBot, types

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
ADMIN_CHAT_ID = os.environ["ADMIN_CHAT_ID"]  # чат/группа модераторов
CHANNEL_ID = os.environ["CHANNEL_ID"]        # например @moykanal или -100xxxxxxxxxx

# Настройки подписи, которая ставится в конце каждого опубликованного тейка
COMMUNITY_HANDLE = os.environ.get("COMMUNITY_HANDLE", "@Socialhostility_confa")
BOT_LINK = os.environ.get("BOT_LINK", "http://t.me/PsychoPromblembot")

TAGS_FILE = "hashtags.json"

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
