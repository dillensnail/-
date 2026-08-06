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
CHANNEL_ID = os.environ["CHANNEL_ID"]        # публичный канал

# Технический чат/канал для анонимизации перед форвардом (по умолчанию ADMIN_CHAT_ID)
STORAGE_CHANNEL_ID = os.environ.get("STORAGE_CHANNEL_ID", ADMIN_CHAT_ID)

# Настройки подписи, которая ставится в конце каждого опубликованного тейка
COMMUNITY_HANDLE = os.environ.get("COMMUNITY_HANDLE", "@PsychoPromblem")
BOT_LINK = os.environ.get("BOT_LINK", "https://t.me/PsychoPromblembot")
SIGNATURE_QUOTE = f'\n\n<blockquote>#тейк | {COMMUNITY_HANDLE} | <a href="{BOT_LINK}">takebot✧⁠*⁠。</a></blockquote>'

RULES_URL = os.environ.get("RULES_URL", "https://t.me/your_rules_link")

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
WINDOW_START_HOUR = 8    
WINDOW_END_HOUR = 22     
POST_GAP_MINUTES = 15    

TAGS_FILE = "hashtags.json"
SCHEDULE_FILE = "schedule.json"
CAT_ARTS_FILE = "cat_arts.json"
PHRASES_FILE = "support_phrases.json"
BANNED_FILE = "banned_users.json"

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")

CATEGORY_LABELS = {
    "расстройство": "🧠 Расстройства",
    "нейроотличие": "🧩 Нейроотличия",
}

# ---------- Список забаненных ----------
def load_banned():
    if not os.path.exists(BANNED_FILE):
        save_banned([])
        return []
    with open(BANNED_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_banned(banned_list):
    with open(BANNED_FILE, "w", encoding="utf-8") as f:
        json.dump(banned_list, f, ensure_ascii=False, indent=2)

BANNED_USERS = load_banned()

def is_banned(user_id):
    return user_id in BANNED_USERS

# ---------- Проверка подписки ----------
def check_subscription(user_id):
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logging.warning(f"Ошибка проверки подписки: {e}")
        return True

def get_sub_keyboard():
    channel_link = CHANNEL_ID.replace("@", "https://t.me/") if str(CHANNEL_ID).startswith("@") else RULES_URL
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📢 Подписаться на канал", url=channel_link))
    kb.add(types.InlineKeyboardButton("🔄 Я подписался", callback_data="check_sub"))
    return kb

# ---------- Хештеги ----------
def load_tags():
    if not os.path.exists(TAGS_FILE):
        default = {
            "расстройство": [
                "депрессия", "тревожноерасстройство", "паническиеатаки", "бар",
                "шар", "прл", "птср", "кптср", "окр", "рпп",
                "дри", "шизофрения", "социофобия",
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

CATEGORY_ALIASES = {
    "расстройство": "расстройство",
    "расстройства": "расстройство",
    "нейроотличие": "нейроотличие",
    "нейроотличия": "нейроотличие",
}


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

# ---------- Фразы / Поддержка ----------
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


@bot.message_handler(commands=["addphrases"])
def cmd_addphrases(message):
    """Массовое добавление: /addphrases и дальше каждая фраза с новой строки."""
    if not is_admin(message.from_user.id):
        return
    lines = message.text.split("\n")
    added = 0
    first_line_rest = lines[0].split(maxsplit=1)
    if len(first_line_rest) > 1 and first_line_rest[1].strip():
        SUPPORT_PHRASES.append(first_line_rest[1].strip())
        added += 1
    for line in lines[1:]:
        line = line.strip()
        if line:
            SUPPORT_PHRASES.append(line)
            added += 1
    if added == 0:
        bot.reply_to(message, "Использование: /addphrases, а дальше каждая фраза с новой строки.")
        return
    save_list_file(PHRASES_FILE, SUPPORT_PHRASES)
    bot.reply_to(message, f"Добавлено фраз: {added}. Всего в списке: {len(SUPPORT_PHRASES)}.")


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

# ---------- Расписание ----------
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
    local = dt.astimezone(MOSCOW_TZ)
    start = local.replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    end = local.replace(hour=WINDOW_END_HOUR, minute=0, second=0, microsecond=0)
    if local < start:
        return start
    if local >= end:
        return start + timedelta(days=1)
    return local

def schedule_add(entry_data):
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
    phrase = random.choice(SUPPORT_PHRASES) if SUPPORT_PHRASES else ""
    art_url = random.choice(CAT_ARTS) if CAT_ARTS else None
    
    full_caption = "Спасибо за тейк."
    if phrase:
        full_caption += f"\n\n{phrase}"
        
    try:
        if art_url:
            bot.send_photo(user_id, art_url, caption=full_caption)
        else:
            bot.send_message(user_id, full_caption)
    except Exception:
        logging.exception("Не удалось отправить открытку поддержки")

def get_post_url(msg):
    channel_id_str = str(CHANNEL_ID)
    if channel_id_str.startswith("@"):
        channel_username = channel_id_str.lstrip("@")
        return f"https://t.me/{channel_username}/{msg.message_id}"
    elif channel_id_str.startswith("-100"):
        clean_id = channel_id_str[4:]
        return f"https://t.me/c/{clean_id}/{msg.message_id}"
    else:
        if msg.chat and msg.chat.username:
            return f"https://t.me/{msg.chat.username}/{msg.message_id}"
        clean_id = channel_id_str.lstrip("-")
        return f"https://t.me/c/{clean_id}/{msg.message_id}"

def publish_entry(entry):
    """
    Публикация через двухшаговый пересыл:
    1. Скопировать в тех-чат/буфер (стирает имя автора, сохраняя премиум-эмодзи и стили).
    2. Переслать из тех-чата в основной канал (создаёт плашку 'Переслано от...').

    ВАЖНО: параметр caption у copy_message работает только для МЕДИА (фото/видео/документ).
    Для обычного текстового сообщения Telegram его игнорирует, а у copy_messages (альбомы)
    такого параметра вообще нет — поэтому в этих двух случаях подпись с хештегами
    отправляется отдельным сообщением сразу следом, а не через caption.
    """
    all_tags = [entry["category"]] + entry["tags"]
    tags_line = "\n" + " ".join(f"#{t}" for t in all_tags)
    footer = f"{SIGNATURE_QUOTE.strip()}{tags_line}"

    user_id = entry["user_id"]
    msg_id = entry["message_id"]

    final_msg = None

    if entry.get("media_group_messages"):
        # copy_messages (альбомы) не поддерживает caption вообще — копируем как есть
        stored_msgs = bot.copy_messages(
            STORAGE_CHANNEL_ID,
            user_id,
            entry["media_group_messages"],
        )
        for m in stored_msgs:
            f_msg = bot.forward_message(CHANNEL_ID, STORAGE_CHANNEL_ID, m.message_id)
            if not final_msg:
                final_msg = f_msg
        bot.send_message(CHANNEL_ID, footer, parse_mode="HTML")

    elif entry.get("content_type") == "text":
        # html_text уже хранит форматирование (в т.ч. premium-эмодзи как <tg-emoji>),
        # поэтому просто собираем текст+подпись в одно сообщение — без форварда.
        original_text = entry.get("text_html", "").strip()
        full_text = f"{original_text}{footer}" if original_text else footer
        final_msg = bot.send_message(CHANNEL_ID, full_text, parse_mode="HTML")

    else:
        # одиночное медиа (фото/видео/документ и т.п.) — caption работает штатно
        original_text = entry.get("text_html", "").strip()
        full_text = f"{original_text}{footer}" if original_text else footer
        stored_msg = bot.copy_message(
            STORAGE_CHANNEL_ID,
            user_id,
            msg_id,
            caption=full_text,
            parse_mode="HTML"
        )
        final_msg = bot.forward_message(CHANNEL_ID, STORAGE_CHANNEL_ID, stored_msg.message_id)

    if final_msg:
        post_url = get_post_url(final_msg)
        try:
            bot.send_message(
                user_id,
                f'🌟 <a href="{post_url}">Ваш тейк опубликован!</a>',
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception:
            logging.exception("Не удалось отправить ссылку автору")

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

# ---------- Временные данные ----------
user_drafts = {}          
pending_review = {}       
review_counter = 0
review_user_id = {}          
admin_thread_messages = {}   
user_thread_messages = {}    

media_groups_lock = threading.Lock()
media_groups_buffer = {}

def is_admin(user_id):
    return user_id in ADMIN_IDS

def build_admin_keyboard(review_id):
    """Порядок кнопок:
       🚫 ЗАБАНИТЬ ВЕЗДЕ | ❌ ОТКЛОНИТЬ
       ⚡ СЕЙЧАС        | ✅ В ОЧЕРЕДЬ (справа)
    """
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🚫 ЗАБАНИТЬ ВЕЗДЕ", callback_data=f"ban:{review_id}"),
        types.InlineKeyboardButton("❌ ОТКЛОНИТЬ", callback_data=f"reject:{review_id}")
    )
    kb.row(
        types.InlineKeyboardButton("⚡️ СЕЙЧАС", callback_data=f"publish_now:{review_id}"),
        types.InlineKeyboardButton("✅ В ОЧЕРЕДЬ", callback_data=f"approve:{review_id}")
    )
    return kb

# ---------- Команды ----------
@bot.message_handler(commands=["start"], func=lambda m: m.chat.type == "private")
def cmd_start(message):
    if is_banned(message.from_user.id):
        bot.send_message(message.chat.id, "❌ Вы заблокированы и не можете использовать бота.")
        return

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📖 Правила", url=RULES_URL))
    
    bot.send_message(
        message.chat.id,
        "🌸 Отправьте свой тейк админам\n\n"
        "Оформлять тейк не надо, выберите хештеги после отправки. "
        "Публикация полностью анонимна!",
        reply_markup=kb,
    )

# ---------- Очередь публикаций ----------
@bot.message_handler(commands=["queue"])
def cmd_queue(message):
    if not is_admin(message.from_user.id):
        return

    with SCHEDULE_LOCK:
        queue = SCHEDULE.get("queue", [])

    if not queue:
        bot.send_message(message.chat.id, "📭 Очередь публикаций пуста.")
        return

    text = f"📋 <b>Всего тейков в очереди: {len(queue)}</b>\n\n"
    kb = types.InlineKeyboardMarkup()

    for idx, item in enumerate(queue):
        p_time = datetime.fromisoformat(item['publish_time']).strftime("%d.%m %H:%M")
        short_text = (item.get("text_html") or "Медиатейк")[:25].replace("\n", " ")
        text += f"<b>{idx + 1}. [{p_time}]</b> #{item['category']} — <i>{short_text}...</i>\n"
        
        kb.row(
            types.InlineKeyboardButton(f"🗑 Удалить #{idx+1}", callback_data=f"q_del:{idx}"),
            types.InlineKeyboardButton(f"⚡ Сейчаc #{idx+1}", callback_data=f"q_pub:{idx}")
        )

    bot.send_message(message.chat.id, text, reply_markup=kb, parse_mode="HTML")

@bot.callback_query_handler(func=lambda c: c.data.startswith("q_del:"))
def cb_q_del(call):
    if not is_admin(call.from_user.id):
        return
    idx = int(call.data.split(":")[1])
    with SCHEDULE_LOCK:
        if 0 <= idx < len(SCHEDULE["queue"]):
            removed = SCHEDULE["queue"].pop(idx)
            save_schedule(SCHEDULE)
            
            # Уведомляем автора об удалении
            try:
                bot.send_message(
                    removed["user_id"],
                    "❌ Ваш тейк был удалён из очереди публикаций по решению администрации."
                )
            except Exception:
                pass

            bot.answer_callback_query(call.id, f"Тейк #{idx+1} удалён!")
            cmd_queue(call.message)
        else:
            bot.answer_callback_query(call.id, "Элемент не найден.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("q_pub:"))
def cb_q_pub(call):
    if not is_admin(call.from_user.id):
        return
    idx = int(call.data.split(":")[1])
    with SCHEDULE_LOCK:
        if 0 <= idx < len(SCHEDULE["queue"]):
            item = SCHEDULE["queue"].pop(idx)
            save_schedule(SCHEDULE)
            try:
                publish_entry(item)
                bot.answer_callback_query(call.id, "Опубликовано!")
                cmd_queue(call.message)
            except Exception:
                bot.answer_callback_query(call.id, "Ошибка при публикации.")
        else:
            bot.answer_callback_query(call.id, "Элемент не найден.")

# ---------- Авто-изменение при изменении сообщения в Telegram ----------
@bot.edited_message_handler(func=lambda m: m.chat.type == "private")
def handle_edited_take(message):
    user_id = message.from_user.id
    new_text = message.html_caption if message.caption else (message.html_text or "")

    for review_id, item in list(pending_review.items()):
        if item["user_id"] == user_id and item["message_id"] == message.message_id:
            item["text_html"] = new_text
            update_admin_review_card(review_id)
            bot.send_message(user_id, "✏️ Ваш тейк автоматически обновлён у модераторов!")
            break

@bot.callback_query_handler(func=lambda c: c.data == "check_sub")
def cb_check_sub(call):
    if check_subscription(call.from_user.id):
        bot.answer_callback_query(call.id, "✅ Подписка подтверждена!")
        bot.send_message(call.from_user.id, "Спасибо за подписку! Теперь вы можете отправить ваш тейк.")
    else:
        bot.answer_callback_query(call.id, "❌ Вы всё ещё не подписаны!", show_alert=True)

# ---------- Отмена тейка пользователем ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("user_cancel:"))
def cb_user_cancel(call):
    review_id = int(call.data.split(":")[1])
    item = pending_review.pop(review_id, None)
    if not item:
        bot.answer_callback_query(call.id, "Тейк уже обработан или опубликован!", show_alert=True)
        return

    admin_msg_id = item.get("admin_msg_id")
    if admin_msg_id:
        try:
            bot.edit_message_text(
                f"❌ <b>Тейк #{review_id} отменён автором.</b>",
                ADMIN_CHAT_ID,
                admin_msg_id
            )
        except Exception:
            pass

    bot.edit_message_text("❌ Ваш тейк отменён и удалён из модерации.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, "Отменено.")

# ---------- Двусторонний диалог (как в Livegram) ----------
# Админ отвечает Reply на сообщение из треда тейка -> уходит юзеру
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


# ---------- Приём сообщений ----------
def process_media_group(mg_id):
    with media_groups_lock:
        data = media_groups_buffer.pop(mg_id, None)
    if not data:
        return
    user_id = data["user_id"]
    
    if is_banned(user_id):
        return

    user_drafts[user_id] = {
        "text_html": data["caption_html"],
        "message_id": data["messages"][0],
        "media_group_messages": data["messages"],
        "content_type": "альбом",
        "category": None,
        "tags": set(),
    }
    send_category_keyboard(user_id)

@bot.message_handler(
    content_types=["text", "photo", "video", "document", "animation", "audio", "voice", "video_note"],
    func=lambda m: (
        m.chat.type == "private"
        and not (m.reply_to_message and m.reply_to_message.message_id in user_thread_messages)
    )
)
def handle_incoming_take(message):
    if message.text and message.text.startswith("/"):
        return

    user_id = message.from_user.id

    if is_banned(user_id):
        bot.send_message(user_id, "❌ Вы заблокированы и не можете отправлять тейки.")
        return

    if not check_subscription(user_id):
        bot.send_message(
            user_id,
            "⚠️ <b>Для отправки тейка необходимо подписаться на наш канал!</b>",
            reply_markup=get_sub_keyboard()
        )
        return

    if message.media_group_id:
        mg_id = message.media_group_id
        with media_groups_lock:
            if mg_id not in media_groups_buffer:
                timer = threading.Timer(0.8, process_media_group, args=[mg_id])
                media_groups_buffer[mg_id] = {
                    "user_id": user_id,
                    "messages": [],
                    "caption_html": "",
                    "timer": timer,
                }
                timer.start()
            
            buf = media_groups_buffer[mg_id]
            buf["messages"].append(message.message_id)
            if message.caption and not buf["caption_html"]:
                buf["caption_html"] = message.html_caption or message.caption
        return

    text_html = message.html_caption if message.caption else (message.html_text or "")
    user_drafts[user_id] = {
        "text_html": text_html,
        "message_id": message.message_id,
        "media_group_messages": None,
        "content_type": message.content_type,
        "category": None,
        "tags": set(),
    }
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

# ---------- Выбор категории и тегов ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("cat:"))
def cb_choose_category(call):
    user_id = call.from_user.id
    if user_id not in user_drafts:
        bot.answer_callback_query(call.id, "Сначала отправьте тейк.")
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

def update_admin_review_card(review_id):
    """Обновление карточки у админов при редактировании исходного сообщения"""
    item = pending_review.get(review_id)
    if not item or not item.get("admin_msg_id"):
        return

    all_tags = [item["category"]] + item["tags"]
    tags_line = "\n" + " ".join(f"#{t}" for t in all_tags)
    user_text = item.get("text_html", "").strip()
    preview_body = f"{user_text}{SIGNATURE_QUOTE}{tags_line}" if user_text else f"{SIGNATURE_QUOTE.strip()}{tags_line}"
    admin_text = f"📝 <b>Тейк на проверке (#{review_id}) [ОБНОВЛЁН АВТОРОМ ✏️]</b>\n\n{preview_body}"

    kb = build_admin_keyboard(review_id)

    try:
        if item.get("media_group_messages") or item.get("content_type") == "text":
            bot.edit_message_text(admin_text, ADMIN_CHAT_ID, item["admin_msg_id"], reply_markup=kb, parse_mode="HTML")
        else:
            # у медиа-сообщений текст хранится как caption — редактируется отдельным методом
            bot.edit_message_caption(admin_text, ADMIN_CHAT_ID, item["admin_msg_id"], reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda c: c.data == "confirm")
def cb_confirm(call):
    global review_counter
    user_id = call.from_user.id
    
    if not check_subscription(user_id):
        bot.send_message(user_id, "⚠️ Вы не подписаны на канал!", reply_markup=get_sub_keyboard())
        bot.answer_callback_query(call.id)
        return

    draft = user_drafts.get(user_id)
    if not draft or not draft["category"]:
        bot.answer_callback_query(call.id, "Черновик не найден.")
        return

    review_counter += 1
    review_id = review_counter
    pending_review[review_id] = {
        "user_id": user_id,
        "text_html": draft["text_html"],
        "message_id": draft["message_id"],
        "media_group_messages": draft["media_group_messages"],
        "content_type": draft["content_type"],
        "category": draft["category"],
        "tags": sorted(draft["tags"]),
        "admin_msg_id": None
    }
    review_user_id[review_id] = user_id
    user_drafts.pop(user_id, None)

    user_control_kb = types.InlineKeyboardMarkup()
    user_control_kb.row(
        types.InlineKeyboardButton("❌ Отменить тейк", callback_data=f"user_cancel:{review_id}")
    )

    bot.edit_message_text(
        "✨ Ваш тейк отправлен на модерацию!\n<i>(Если захотите его изменить — просто отредактируйте своё отправленное сообщение в этом чате)</i>",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=user_control_kb,
        parse_mode="HTML"
    )
    bot.answer_callback_query(call.id)
    
    send_support_gift(user_id)

    item = pending_review[review_id]
    all_tags = [item["category"]] + item["tags"]
    tags_line = "\n" + " ".join(f"#{t}" for t in all_tags)
    
    user_text = item.get("text_html", "").strip()
    preview_body = f"{user_text}{SIGNATURE_QUOTE}{tags_line}" if user_text else f"{SIGNATURE_QUOTE.strip()}{tags_line}"
    admin_text = f"📝 <b>Новый тейк на проверку (#{review_id})</b>\n\n{preview_body}"
    
    kb = build_admin_keyboard(review_id)

    if item.get("media_group_messages"):
        bot.copy_messages(ADMIN_CHAT_ID, user_id, item["media_group_messages"])
        admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_text, reply_markup=kb, parse_mode="HTML")
    elif item.get("content_type") == "text":
        # admin_text уже содержит текст тейка + подпись + теги одним блоком
        admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_text, reply_markup=kb, parse_mode="HTML")
    else:
        admin_msg = bot.copy_message(
            ADMIN_CHAT_ID, user_id, item["message_id"],
            caption=admin_text, reply_markup=kb, parse_mode="HTML"
        )

    pending_review[review_id]["admin_msg_id"] = admin_msg.message_id
    admin_thread_messages[admin_msg.message_id] = review_id

# ---------- Модерация и Бан ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith("ban:"))
def cb_ban_user(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return

    review_id = int(call.data.split(":", 1)[1])
    item = pending_review.pop(review_id, None)
    target_user_id = item["user_id"] if item else review_user_id.get(review_id)
    
    if not target_user_id:
        bot.answer_callback_query(call.id, "Пользователь не найден.")
        return

    if target_user_id not in BANNED_USERS:
        BANNED_USERS.append(target_user_id)
        save_banned(BANNED_USERS)

    kicked_from_chat = False
    try:
        bot.ban_chat_member(ADMIN_CHAT_ID, target_user_id)
        kicked_from_chat = True
    except Exception as e:
        logging.warning(f"Не удалось исключить из КФ: {e}")

    admin_name = html.escape(call.from_user.first_name or call.from_user.username or "админ")
    chat_status = "и выбит из чата 🚪" if kicked_from_chat else "(не был в чате / нет прав)"
    
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup())
    bot.send_message(
        call.message.chat.id,
        f"🚫 <b>Пользователь {target_user_id} забанен {chat_status}</b> ({admin_name}).",
        reply_to_message_id=call.message.message_id
    )
    bot.answer_callback_query(call.id, "Забанен!")

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
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup())
    bot.send_message(call.message.chat.id, f"✅ Принято ({admin_name}), запланировано на {time_str} (мск).", reply_to_message_id=call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("publish_now:"))
def cb_publish_now_review(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = int(call.data.split(":", 1)[1])
    item = pending_review.pop(review_id, None)
    if not item:
        bot.answer_callback_query(call.id, "Уже обработано.")
        return

    admin_name = html.escape(call.from_user.first_name or call.from_user.username or "админ")
    try:
        publish_entry(item)
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup())
        bot.send_message(call.message.chat.id, f"⚡️ Опубликовано мгновенно ({admin_name}).", reply_to_message_id=call.message.message_id)
        bot.answer_callback_query(call.id, "Опубликовано!")
    except Exception:
        logging.exception("Ошибка мгновенной публикации")
        bot.answer_callback_query(call.id, "Ошибка при публикации.")

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
    bot.send_message(
        item["user_id"],
        "Ваш тейк отклонён за нарушение правил или по решению администрации."
    )
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup())
    bot.send_message(call.message.chat.id, f"❌ Отклонено ({admin_name}).", reply_to_message_id=call.message.message_id)
    bot.answer_callback_query(call.id)

if __name__ == "__main__":
    print("Bot started")
    threading.Thread(target=scheduler_loop, daemon=True).start()
    bot.infinity_polling()