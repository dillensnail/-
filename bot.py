import os
import json
import html
import logging
import random
import signal
import sys
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telebot import TeleBot, types, apihelper
from telebot.apihelper import ApiTelegramException

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
# Главные админы: могут банить, менять хештеги/картинки/фразы. Если не задано — совпадает с ADMIN_IDS
# (то есть по умолчанию все обычные админы имеют полные права, ничего не сломается для тех, кто не настроит это отдельно).
SUPER_ADMIN_IDS = [int(x) for x in os.environ.get("SUPER_ADMIN_IDS", "").split(",") if x.strip()] or ADMIN_IDS
ADMIN_CHAT_ID = os.environ["ADMIN_CHAT_ID"]  # чат/группа модераторов
CHANNEL_ID = os.environ["CHANNEL_ID"]        # публичный канал

# Технический чат/канал для анонимизации перед форвардом (по умолчанию ADMIN_CHAT_ID)
STORAGE_CHANNEL_ID = os.environ.get("STORAGE_CHANNEL_ID", ADMIN_CHAT_ID)

# Настройки подписи, которая ставится в конце каждого опубликованного тейка
COMMUNITY_HANDLE = os.environ.get("COMMUNITY_HANDLE", "@PsychoPromblem")
BOT_LINK = os.environ.get("BOT_LINK", "https://t.me/PsychoPromblembot")
SIGNATURE_QUOTE = f'\n\n<blockquote>#тейк | {COMMUNITY_HANDLE} | <a href="{BOT_LINK}">Send take now✧⁠*⁠。</a></blockquote>'

# Теги, которые при публикации выносятся отдельной строкой предупреждения "⚠️ TW/CW: ..." в начало поста.
# Легко редактируемый список — добавляй/убирай теги и их человекочитаемые подписи здесь.
TW_CW_LABELS = {
    "селфхарм": "селфхарм",
    "суицидальныемысли": "суицидальные мысли",
}


def build_tw_cw_line(tags: list) -> str:
    """Строка-предупреждение о чувствительном контенте для начала поста (или пустая строка, если нет таких тегов)."""
    labels = [TW_CW_LABELS[t] for t in tags if t in TW_CW_LABELS]
    if not labels:
        return ""
    return f"⚠️ <b>TW/CW:</b> {', '.join(labels)}\n\n"

RULES_URL = os.environ.get("RULES_URL", "https://t.me/your_rules_link")
# Ссылка-приглашение на канал для кнопки "Подписаться". Обязательно задай явно, если CHANNEL_ID —
# числовой (закрытый канал без @username), иначе ссылку не получится определить автоматически.
CHANNEL_URL = os.environ.get("CHANNEL_URL", "")
# Публичный username канала для формирования ссылок на посты (https://t.me/<username>/<msg_id>).
CHANNEL_USERNAME_FOR_LINKS = os.environ.get("CHANNEL_USERNAME_FOR_LINKS", "MeshiMeshu")

# ---------- Именованные константы (вместо магических строк) ----------
CONTENT_TYPE_TEXT = "text"
CONTENT_TYPE_ALBUM = "альбом"

CB_PREFIX_CATEGORY = "cat:"
CB_PREFIX_TAG = "tag:"
CB_PREFIX_APPROVE = "approve:"
CB_PREFIX_REJECT = "reject:"
CB_PREFIX_BAN = "ban:"
CB_PREFIX_PUBLISH_NOW = "publish_now:"
CB_PREFIX_USER_CANCEL = "user_cancel:"
CB_PREFIX_QUEUE_DEL = "q_del:"
CB_PREFIX_QUEUE_PUB = "q_pub:"
CB_CANCEL = "cancel"
CB_CONFIRM = "confirm"
CB_CHECK_SUB = "check_sub"

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

# Встроенные ретраи pyTelegramBotAPI: при 429 (Too Many Requests) и временных сетевых
# сбоях библиотека сама подождёт и повторит запрос вместо немедленного исключения.
apihelper.RETRY_ON_ERROR = True
apihelper.RETRY_TIMEOUT = 5
apihelper.MAX_RETRIES = 3

# Общая блокировка для структур, разделяемых между потоками-обработчиками апдейтов
# (TeleBot по умолчанию threaded=True — на каждый апдейт может быть отдельный поток).
# Один общий RLock проще в рассуждении (без риска дедлоков из-за порядка блокировок)
# и для масштаба одного сообщества этого достаточно; при росте нагрузки имеет смысл
# разнести на более гранулярные локи по структурам.
DATA_LOCK = threading.RLock()


def atomic_write_json(path: str, data) -> None:
    """Запись JSON через временный файл + rename — исключает повреждение файла,
    если процесс убьют посреди записи (например, при редеплое на Railway)."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def get_entry_categories(entry: dict) -> list:
    """Список категорий тейка. Поддерживает и новый формат ('categories': [...]),
    и старые уже сохранённые записи в schedule.json/review_state.json с одиночной 'category'."""
    if entry.get("categories"):
        return list(entry["categories"])
    if entry.get("category"):
        return [entry["category"]]
    return []

CATEGORY_LABELS = {
    "расстройство": "🧠 Расстройства",
    "нейроотличие": "🧩 Нейроотличия",
    "дополнительно": "➕ Дополнительно",
}
CATEGORY_ORDER = ["расстройство", "нейроотличие", "дополнительно"]

# Теги-предупреждения о чувствительном контенте — при выборе юзеру мягко покажем ресурсы поддержки
SENSITIVE_TAGS = {"селфхарм", "суицидальныемысли"}
CRISIS_NOTE = (
    "Если тебе прямо сейчас тяжело — есть, куда обратиться:\n\n"
    "☎️ Экстренная психологическая помощь (Россия, 7:00–23:00 мск, бесплатно):\n"
    "<code>8-800-200-31-00</code>\n"
    "<code>+7 499 288-31-00</code>\n\n"
    "🤖 Бот @DBT_Skills_Bot — кнопка SOS\n\n"
    'Полная подборка горячих линий и бесплатных консультаций (в т.ч. Украина, Беларусь): '
    '<a href="https://t.me/MeshiMeshu/3">пост с ресурсами</a>\n\n'
    "Ты не обязан(а) справляться с этим в одиночку."
)

# ---------- Список забаненных ----------
def load_banned() -> list:
    if not os.path.exists(BANNED_FILE):
        save_banned([])
        return []
    with open(BANNED_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_banned(banned_list: list) -> None:
    atomic_write_json(BANNED_FILE, banned_list)

BANNED_USERS = load_banned()

def is_banned(user_id: int) -> bool:
    return user_id in BANNED_USERS

# ---------- Проверка подписки ----------
def check_subscription(user_id: int) -> bool:
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["creator", "administrator", "member"]
    except ApiTelegramException as e:
        # Fail-open осознанно: сбой проверки подписки не должен блокировать приём тейков
        # полностью. Но уровень error — чтобы это было видно как инцидент, а не фон.
        logging.error(f"Проверка подписки не удалась (fail-open, доступ разрешён): {e}")
        return True

def get_channel_url() -> str:
    if CHANNEL_URL:
        return CHANNEL_URL
    channel_id_str = str(CHANNEL_ID)
    if channel_id_str.startswith("@"):
        return f"https://t.me/{channel_id_str.lstrip('@')}"
    try:
        chat = bot.get_chat(CHANNEL_ID)
        if chat.invite_link:
            return chat.invite_link
        if chat.username:
            return f"https://t.me/{chat.username}"
    except ApiTelegramException:
        logging.warning("Не удалось получить ссылку на канал автоматически — задай CHANNEL_URL вручную")
    return RULES_URL  # запасной вариант, если ничего не сработало


def get_sub_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📢 Подписаться на канал", url=get_channel_url()))
    kb.add(types.InlineKeyboardButton("🔄 Я подписался", callback_data=CB_CHECK_SUB))
    return kb

# ---------- Хештеги ----------
def load_tags() -> dict:
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
            "дополнительно": [
                "интерактив", "полезное", "селфхарм", "ремиссия",
                "суицидальныемысли", "другое",
            ],
        }
        save_tags(default)
        return default
    with open(TAGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_tags(tags: dict) -> None:
    atomic_write_json(TAGS_FILE, tags)

TAGS = load_tags()

CATEGORY_ALIASES = {
    "расстройство": "расстройство",
    "расстройства": "расстройство",
    "нейроотличие": "нейроотличие",
    "нейроотличия": "нейроотличие",
    "дополнительно": "дополнительно",
    "доп": "дополнительно",
}


@bot.message_handler(commands=["addtag"])
def cmd_addtag(message: types.Message) -> None:
    if not is_super_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1].lower() not in CATEGORY_ALIASES:
        bot.reply_to(message, "Использование: /addtag расстройство|нейроотличие|дополнительно название")
        return
    category = CATEGORY_ALIASES[parts[1].lower()]
    tag = parts[2].strip().lstrip("#").lower()
    with DATA_LOCK:
        if tag in TAGS[category]:
            bot.reply_to(message, f"Тег #{tag} уже есть в категории «{category}».")
            return
        TAGS[category].append(tag)
        save_tags(TAGS)
    bot.reply_to(message, f"Добавлен тег #{tag} в категорию «{category}»")


@bot.message_handler(commands=["deltag"])
def cmd_deltag(message: types.Message) -> None:
    if not is_super_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1].lower() not in CATEGORY_ALIASES:
        bot.reply_to(message, "Использование: /deltag расстройство|нейроотличие|дополнительно название")
        return
    category = CATEGORY_ALIASES[parts[1].lower()]
    tag = parts[2].strip().lstrip("#").lower()
    with DATA_LOCK:
        if tag not in TAGS[category]:
            bot.reply_to(message, f"Тега #{tag} нет в категории «{category}».")
            return
        TAGS[category].remove(tag)
        save_tags(TAGS)
    bot.reply_to(message, f"Удалён тег #{tag} из категории «{category}»")


@bot.message_handler(commands=["taglist"])
def cmd_taglist(message: types.Message) -> None:
    lines = []
    for category, tags in TAGS.items():
        lines.append(f"«{category}»: " + " ".join(f"#{t}" for t in tags))
    bot.reply_to(message, "\n".join(lines))

# ---------- Фразы / Поддержка ----------
def load_list_file(path: str, default: list) -> list:
    if not os.path.exists(path):
        save_list_file(path, default)
        return list(default)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_list_file(path: str, data: list) -> None:
    atomic_write_json(path, data)

CAT_ARTS = load_list_file(CAT_ARTS_FILE, [])
SUPPORT_PHRASES = load_list_file(PHRASES_FILE, [
    "Ты не один.",
    "Спасибо, что поделился — ты значимый.",
    "Спасибо за тейк. Помни: тебе не обязательно сворачивать горы, чтобы тебя любили.",
    "Твои чувства имеют значение.",
    "Ты имеешь право на поддержку и заботу, просто потому что ты есть.",
])


@bot.message_handler(commands=["addcatart"])
def cmd_addcatart(message: types.Message) -> None:
    if not is_super_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().startswith("http"):
        bot.reply_to(message, "Использование: /addcatart <прямая ссылка на картинку>")
        return
    url = parts[1].strip()
    with DATA_LOCK:
        CAT_ARTS.append(url)
        save_list_file(CAT_ARTS_FILE, CAT_ARTS)
    bot.reply_to(message, f"Добавлено. Всего картинок: {len(CAT_ARTS)}.")


@bot.message_handler(commands=["delcatart"])
def cmd_delcatart(message: types.Message) -> None:
    if not is_super_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        bot.reply_to(message, "Использование: /delcatart номер (номера смотри в /catartlist)")
        return
    idx = int(parts[1].strip()) - 1
    with DATA_LOCK:
        if 0 <= idx < len(CAT_ARTS):
            removed = CAT_ARTS.pop(idx)
            save_list_file(CAT_ARTS_FILE, CAT_ARTS)
            bot.reply_to(message, f"Удалено: {removed}")
        else:
            bot.reply_to(message, "Нет такого номера.")


@bot.message_handler(commands=["catartlist"])
def cmd_catartlist(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        return
    if not CAT_ARTS:
        bot.reply_to(message, "Список пуст. Добавь картинки: /addcatart <ссылка>")
        return
    lines = [f"{i + 1}. {u}" for i, u in enumerate(CAT_ARTS)]
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["addphrase"])
def cmd_addphrase(message: types.Message) -> None:
    if not is_super_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Использование: /addphrase текст фразы")
        return
    with DATA_LOCK:
        SUPPORT_PHRASES.append(parts[1].strip())
        save_list_file(PHRASES_FILE, SUPPORT_PHRASES)
    bot.reply_to(message, "Фраза добавлена.")


@bot.message_handler(commands=["addphrases"])
def cmd_addphrases(message: types.Message) -> None:
    """Массовое добавление: /addphrases и дальше каждая фраза с новой строки."""
    if not is_super_admin(message.from_user.id):
        return
    lines = message.text.split("\n")
    added = 0
    with DATA_LOCK:
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
def cmd_delphrase(message: types.Message) -> None:
    if not is_super_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        bot.reply_to(message, "Использование: /delphrase номер (номера смотри в /phraselist)")
        return
    idx = int(parts[1].strip()) - 1
    with DATA_LOCK:
        if 0 <= idx < len(SUPPORT_PHRASES):
            removed = SUPPORT_PHRASES.pop(idx)
            save_list_file(PHRASES_FILE, SUPPORT_PHRASES)
            bot.reply_to(message, f"Удалено: {removed}")
        else:
            bot.reply_to(message, "Нет такого номера.")


@bot.message_handler(commands=["phraselist"])
def cmd_phraselist(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        return
    if not SUPPORT_PHRASES:
        bot.reply_to(message, "Список пуст.")
        return
    lines = [f"{i + 1}. {p}" for i, p in enumerate(SUPPORT_PHRASES)]
    bot.reply_to(message, "\n".join(lines))

# ---------- Расписание ----------
def load_schedule() -> dict:
    if not os.path.exists(SCHEDULE_FILE):
        default = {"next_slot": datetime.now(MOSCOW_TZ).isoformat(), "queue": []}
        save_schedule(default)
        return default
    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_schedule(state: dict) -> None:
    atomic_write_json(SCHEDULE_FILE, state)

SCHEDULE = load_schedule()
SCHEDULE_LOCK = threading.Lock()

def clamp_to_window(dt: datetime) -> datetime:
    local = dt.astimezone(MOSCOW_TZ)
    start = local.replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    end = local.replace(hour=WINDOW_END_HOUR, minute=0, second=0, microsecond=0)
    if local < start:
        return start
    if local >= end:
        return start + timedelta(days=1)
    return local

def schedule_add(entry_data: dict) -> datetime:
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

def send_support_gift(user_id: int) -> None:
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
    except ApiTelegramException:
        logging.exception("Не удалось отправить открытку поддержки")

def get_post_url(msg: types.Message) -> str:
    """Ссылка на опубликованный пост — всегда через публичный username канала."""
    return f"https://t.me/{CHANNEL_USERNAME_FOR_LINKS}/{msg.message_id}"

def publish_entry(entry: dict) -> None:
    """
    Публикация через двухшаговый пересыл:
    1. Скопировать в тех-чат/буфер (стирает имя автора, сохраняя премиум-эмодзи и стили).
    2. Переслать из тех-чата в основной канал (создаёт плашку 'Переслано от...').

    ВАЖНО: параметр caption у copy_message работает только для МЕДИА (фото/видео/документ).
    Для обычного текстового сообщения Telegram его игнорирует, а у copy_messages (альбомы)
    такого параметра вообще нет — поэтому в этих двух случаях подпись с хештегами
    отправляется отдельным сообщением сразу следом, а не через caption.
    """
    tags = entry.get("tags", [])
    all_tags = get_entry_categories(entry) + tags
    tags_line = "\n" + " ".join(f"#{t}" for t in all_tags)
    footer = f"{SIGNATURE_QUOTE.strip()}{tags_line}"
    tw_cw = build_tw_cw_line(tags)

    user_id = entry["user_id"]
    msg_id = entry.get("message_id")
    content_type = entry.get("content_type")

    final_msg = None

    if entry.get("media_group_messages"):
        # copy_messages (альбомы) не поддерживает caption вообще — копируем как есть,
        # а TW/CW и подпись с хештегами шлём отдельными сообщениями (до и после альбома).
        if tw_cw:
            try:
                bot.send_message(CHANNEL_ID, tw_cw.strip(), parse_mode="HTML")
            except ApiTelegramException as e:
                raise RuntimeError(f"Не удалось отправить TW/CW в CHANNEL_ID={CHANNEL_ID}: {e}") from e
        try:
            stored_msgs = bot.copy_messages(STORAGE_CHANNEL_ID, user_id, entry["media_group_messages"])
        except ApiTelegramException as e:
            raise RuntimeError(f"Не удалось скопировать альбом в STORAGE_CHANNEL_ID={STORAGE_CHANNEL_ID}: {e}") from e
        for m in stored_msgs:
            try:
                f_msg = bot.forward_message(CHANNEL_ID, STORAGE_CHANNEL_ID, m.message_id)
            except ApiTelegramException as e:
                raise RuntimeError(f"Не удалось переслать альбом в CHANNEL_ID={CHANNEL_ID}: {e}") from e
            if not final_msg:
                final_msg = f_msg
        try:
            bot.send_message(CHANNEL_ID, footer, parse_mode="HTML")
        except ApiTelegramException as e:
            raise RuntimeError(f"Не удалось отправить подпись в CHANNEL_ID={CHANNEL_ID}: {e}") from e

    elif content_type == CONTENT_TYPE_TEXT:
        # html_text уже хранит форматирование (в т.ч. premium-эмодзи как <tg-emoji>),
        # поэтому просто собираем tw_cw+текст+подпись в одно сообщение — без форварда.
        original_text = entry.get("text_html", "").strip()
        full_text = f"{tw_cw}{original_text}{footer}" if original_text else f"{tw_cw}{footer}"
        try:
            final_msg = bot.send_message(CHANNEL_ID, full_text, parse_mode="HTML")
        except ApiTelegramException as e:
            raise RuntimeError(f"Не удалось опубликовать текст в CHANNEL_ID={CHANNEL_ID}: {e}") from e

    else:
        # одиночное медиа (фото/видео/документ и т.п.) — caption работает штатно
        original_text = entry.get("text_html", "").strip()
        full_text = f"{tw_cw}{original_text}{footer}" if original_text else f"{tw_cw}{footer}"
        try:
            stored_msg = bot.copy_message(
                STORAGE_CHANNEL_ID, user_id, msg_id, caption=full_text, parse_mode="HTML"
            )
        except ApiTelegramException as e:
            raise RuntimeError(f"Не удалось скопировать медиа в STORAGE_CHANNEL_ID={STORAGE_CHANNEL_ID}: {e}") from e
        try:
            final_msg = bot.forward_message(CHANNEL_ID, STORAGE_CHANNEL_ID, stored_msg.message_id)
        except ApiTelegramException as e:
            raise RuntimeError(f"Не удалось переслать медиа в CHANNEL_ID={CHANNEL_ID}: {e}") from e

    if final_msg:
        post_url = get_post_url(final_msg)
        try:
            bot.send_message(
                user_id,
                f'🌟 <a href="{post_url}">Ваш тейк опубликован!</a>',
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except ApiTelegramException:
            logging.exception("Не удалось отправить ссылку автору")

shutdown_event = threading.Event()

def scheduler_loop() -> None:
    global SCHEDULE
    while not shutdown_event.is_set():
        try:
            with SCHEDULE_LOCK:
                now = datetime.now(MOSCOW_TZ)
                changed = False
                while SCHEDULE["queue"] and datetime.fromisoformat(SCHEDULE["queue"][0]["publish_time"]) <= now:
                    entry = SCHEDULE["queue"].pop(0)
                    changed = True
                    try:
                        publish_entry(entry)
                    except (ApiTelegramException, RuntimeError) as e:
                        logging.exception("Не удалось опубликовать тейк из очереди")
                        try:
                            bot.send_message(
                                ADMIN_CHAT_ID,
                                f"⚠️ Не удалось автоматически опубликовать тейк из очереди "
                                f"(автор id={entry.get('user_id')}): {html.escape(str(e))}\n"
                                f"Тейк потерян из очереди — если нужно, попроси автора отправить заново."
                            )
                        except ApiTelegramException:
                            pass
                if changed:
                    save_schedule(SCHEDULE)
        except Exception as e:
            logging.exception("Ошибка в цикле планировщика")
            try:
                bot.send_message(ADMIN_CHAT_ID, f"⚠️ Ошибка в планировщике публикаций: {html.escape(str(e))}")
            except ApiTelegramException:
                pass
        shutdown_event.wait(20)

# ---------- Временные данные ----------
REVIEW_STATE_FILE = "review_state.json"


def load_review_state() -> dict:
    if not os.path.exists(REVIEW_STATE_FILE):
        return {
            "review_counter": 0,
            "pending_review": {},
            "review_user_id": {},
            "admin_thread_messages": {},
            "user_thread_messages": {},
        }
    with open(REVIEW_STATE_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    raw["pending_review"] = {int(k): v for k, v in raw.get("pending_review", {}).items()}
    raw["review_user_id"] = {int(k): v for k, v in raw.get("review_user_id", {}).items()}
    raw["admin_thread_messages"] = {int(k): v for k, v in raw.get("admin_thread_messages", {}).items()}
    raw["user_thread_messages"] = {int(k): v for k, v in raw.get("user_thread_messages", {}).items()}
    return raw


def save_review_state() -> None:
    data = {
        "review_counter": review_counter,
        "pending_review": pending_review,
        "review_user_id": review_user_id,
        "admin_thread_messages": admin_thread_messages,
        "user_thread_messages": user_thread_messages,
    }
    atomic_write_json(REVIEW_STATE_FILE, data)


# Черновики (текст+категория+теги до нажатия "Подтвердить") не переживают перезапуск —
# это нормально, юзеру достаточно просто отправить текст ещё раз.
# А вот то, что уже ушло на модерацию (pending_review) и все связи для диалога — сохраняются.
user_drafts = {}
_review_state = load_review_state()
review_counter = _review_state["review_counter"]
pending_review = _review_state["pending_review"]
review_user_id = _review_state["review_user_id"]
admin_thread_messages = _review_state["admin_thread_messages"]
user_thread_messages = _review_state["user_thread_messages"]

media_groups_lock = threading.Lock()
media_groups_buffer = {}

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS

def build_admin_keyboard(review_id: int) -> types.InlineKeyboardMarkup:
    """Порядок кнопок:
       🚫 ЗАБАНИТЬ ВЕЗДЕ | ❌ ОТКЛОНИТЬ
       ⚡ СЕЙЧАС        | ✅ В ОЧЕРЕДЬ (справа)
    """
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🚫 ЗАБАНИТЬ ВЕЗДЕ", callback_data=f"{CB_PREFIX_BAN}{review_id}"),
        types.InlineKeyboardButton("❌ ОТКЛОНИТЬ", callback_data=f"{CB_PREFIX_REJECT}{review_id}")
    )
    kb.row(
        types.InlineKeyboardButton("⚡️ СЕЙЧАС", callback_data=f"{CB_PREFIX_PUBLISH_NOW}{review_id}"),
        types.InlineKeyboardButton("✅ В ОЧЕРЕДЬ", callback_data=f"{CB_PREFIX_APPROVE}{review_id}")
    )
    return kb

# ---------- Команды ----------
@bot.message_handler(commands=["start"], func=lambda m: m.chat.type == "private")
def cmd_start(message: types.Message) -> None:
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
def cmd_queue(message: types.Message) -> None:
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
        cats_str = " ".join(f"#{c}" for c in get_entry_categories(item))
        text += f"<b>{idx + 1}. [{p_time}]</b> {cats_str} — <i>{short_text}...</i>\n"
        
        kb.row(
            types.InlineKeyboardButton(f"🗑 Удалить #{idx+1}", callback_data=f"{CB_PREFIX_QUEUE_DEL}{idx}"),
            types.InlineKeyboardButton(f"⚡ Сейчаc #{idx+1}", callback_data=f"{CB_PREFIX_QUEUE_PUB}{idx}")
        )

    bot.send_message(message.chat.id, text, reply_markup=kb, parse_mode="HTML")

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_QUEUE_DEL))
def cb_q_del(call: types.CallbackQuery) -> None:
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
            except ApiTelegramException:
                pass

            bot.answer_callback_query(call.id, f"Тейк #{idx+1} удалён!")
            cmd_queue(call.message)
        else:
            bot.answer_callback_query(call.id, "Элемент не найден.")

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_QUEUE_PUB))
def cb_q_pub(call: types.CallbackQuery) -> None:
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
            except (ApiTelegramException, RuntimeError):
                bot.answer_callback_query(call.id, "Ошибка при публикации.")
        else:
            bot.answer_callback_query(call.id, "Элемент не найден.")

# ---------- Авто-изменение при изменении сообщения в Telegram ----------
@bot.edited_message_handler(func=lambda m: m.chat.type == "private")
def handle_edited_take(message: types.Message) -> None:
    user_id = message.from_user.id
    new_text = message.html_caption if message.caption else (message.html_text or "")

    with DATA_LOCK:
        matched_review_id = None
        for review_id, item in list(pending_review.items()):
            if item["user_id"] == user_id and item["message_id"] == message.message_id:
                item["text_html"] = new_text
                save_review_state()
                matched_review_id = review_id
                break
    if matched_review_id is not None:
        update_admin_review_card(matched_review_id)
        bot.send_message(user_id, "✏️ Ваш тейк автоматически обновлён у модераторов!")

@bot.callback_query_handler(func=lambda c: c.data == CB_CHECK_SUB)
def cb_check_sub(call: types.CallbackQuery) -> None:
    if check_subscription(call.from_user.id):
        bot.answer_callback_query(call.id, "✅ Подписка подтверждена!")
        bot.send_message(call.from_user.id, "Спасибо за подписку! Теперь вы можете отправить ваш тейк.")
    else:
        bot.answer_callback_query(call.id, "❌ Вы всё ещё не подписаны!", show_alert=True)

# ---------- Отмена тейка пользователем ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_USER_CANCEL))
def cb_user_cancel(call: types.CallbackQuery) -> None:
    review_id = int(call.data.split(":")[1])
    item = pop_review_and_save(review_id)
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
        except ApiTelegramException:
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
def handle_admin_reply(message: types.Message) -> None:
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
        with DATA_LOCK:
            save_review_state()
        bot.reply_to(message, "✅ Ответ отправлен пользователю.")
    except ApiTelegramException:
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
def handle_user_reply(message: types.Message) -> None:
    review_id = user_thread_messages[message.reply_to_message.message_id]
    user_name = html.escape(message.from_user.first_name or message.from_user.username or "пользователь")
    text = html.escape(message.text)
    sent = bot.send_message(
        ADMIN_CHAT_ID,
        f"↩️ Ответ от {user_name} по тейку #{review_id}:\n\n{text}"
    )
    admin_thread_messages[sent.message_id] = review_id
    with DATA_LOCK:
        save_review_state()
    bot.reply_to(message, "Сообщение отправлено администрации.")


# ---------- Приём сообщений ----------
def process_media_group(mg_id: str) -> None:
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
        "content_type": CONTENT_TYPE_ALBUM,
        "categories": set(),
        "tags": set(),
        "crisis_note_sent": False,
    }
    send_category_keyboard(user_id)

@bot.message_handler(
    content_types=["text", "photo", "video", "document", "animation", "audio", "voice", "video_note"],
    func=lambda m: (
        m.chat.type == "private"
        and not (m.reply_to_message and m.reply_to_message.message_id in user_thread_messages)
    )
)
def handle_incoming_take(message: types.Message) -> None:
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
                timer = threading.Timer(1.5, process_media_group, args=[mg_id])
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
        "categories": set(),
        "tags": set(),
        "crisis_note_sent": False,
    }
    send_category_keyboard(message.chat.id)

def build_picker_keyboard(draft: dict) -> types.InlineKeyboardMarkup:
    """Один экран: категории сверху (мультивыбор), теги выбранных категорий сразу под ними —
    без промежуточной кнопки 'Далее', список тегов обновляется сразу при тапе на категорию."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    for key in CATEGORY_ORDER:
        prefix = "✅ " if key in draft["categories"] else ""
        kb.add(types.InlineKeyboardButton(f"{prefix}{CATEGORY_LABELS[key]}", callback_data=f"{CB_PREFIX_CATEGORY}{key}"))

    tag_buttons = []
    for category in CATEGORY_ORDER:
        if category not in draft["categories"]:
            continue
        for tag in TAGS.get(category, []):
            label = f"✅ #{tag}" if tag in draft["tags"] else f"#{tag}"
            tag_buttons.append(types.InlineKeyboardButton(label, callback_data=f"{CB_PREFIX_TAG}{tag}"))
    if tag_buttons:
        kb.add(*tag_buttons)

    kb.row(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data=CB_CONFIRM),
        types.InlineKeyboardButton("❌ Отменить", callback_data=CB_CANCEL),
    )
    return kb

def send_category_keyboard(chat_id: int) -> None:
    draft = user_drafts[chat_id]
    bot.send_message(
        chat_id,
        "Выберите категории и теги (можно несколько):",
        reply_markup=build_picker_keyboard(draft)
    )

# ---------- Выбор категорий и тегов (один экран, без "Далее") ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_CATEGORY))
def cb_choose_category(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft:
        bot.answer_callback_query(call.id, "Сначала отправьте тейк.")
        return
    category = call.data.split(":", 1)[1]
    if category in draft["categories"]:
        draft["categories"].remove(category)
        # если сняли категорию — снимаем и теги, которые относились только к ней
        draft["tags"] -= set(TAGS.get(category, []))
    else:
        draft["categories"].add(category)
    bot.edit_message_text(
        "Выберите категории и теги (можно несколько):",
        call.message.chat.id, call.message.message_id,
        reply_markup=build_picker_keyboard(draft)
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_TAG))
def cb_toggle_tag(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft or not draft["categories"]:
        bot.answer_callback_query(call.id, "Сначала выберите категорию.")
        return
    tag = call.data.split(":", 1)[1]
    tags = draft["tags"]
    just_added = tag not in tags
    if just_added:
        tags.add(tag)
    else:
        tags.remove(tag)
    bot.edit_message_reply_markup(
        call.message.chat.id, call.message.message_id,
        reply_markup=build_picker_keyboard(draft)
    )
    bot.answer_callback_query(call.id)
    if just_added and tag in SENSITIVE_TAGS and not draft.get("crisis_note_sent"):
        draft["crisis_note_sent"] = True
        try:
            bot.send_message(user_id, CRISIS_NOTE, parse_mode="HTML", disable_web_page_preview=True)
        except ApiTelegramException:
            pass

@bot.callback_query_handler(func=lambda c: c.data == CB_CANCEL)
def cb_cancel(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    user_drafts.pop(user_id, None)
    bot.edit_message_text("Отменено.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

def update_admin_review_card(review_id: int) -> None:
    """Обновление карточки у админов при редактировании исходного сообщения"""
    item = pending_review.get(review_id)
    if not item or not item.get("admin_msg_id"):
        return

    all_tags = get_entry_categories(item) + item.get("tags", [])
    tags_line = "\n" + " ".join(f"#{t}" for t in all_tags)
    user_text = item.get("text_html", "").strip()
    preview_body = f"{user_text}{SIGNATURE_QUOTE}{tags_line}" if user_text else f"{SIGNATURE_QUOTE.strip()}{tags_line}"
    admin_text = f"📝 <b>Тейк на проверке (#{review_id}) [ОБНОВЛЁН АВТОРОМ ✏️]</b>\n\n{preview_body}"

    kb = build_admin_keyboard(review_id)

    try:
        if item.get("media_group_messages") or item.get("content_type") == CONTENT_TYPE_TEXT:
            bot.edit_message_text(admin_text, ADMIN_CHAT_ID, item["admin_msg_id"], reply_markup=kb, parse_mode="HTML")
        else:
            # у медиа-сообщений текст хранится как caption — редактируется отдельным методом
            bot.edit_message_caption(admin_text, ADMIN_CHAT_ID, item["admin_msg_id"], reply_markup=kb, parse_mode="HTML")
    except ApiTelegramException:
        pass

@bot.callback_query_handler(func=lambda c: c.data == CB_CONFIRM)
def cb_confirm(call: types.CallbackQuery) -> None:
    global review_counter
    user_id = call.from_user.id
    
    if not check_subscription(user_id):
        bot.send_message(user_id, "⚠️ Вы не подписаны на канал!", reply_markup=get_sub_keyboard())
        bot.answer_callback_query(call.id)
        return

    draft = user_drafts.get(user_id)
    if not draft or not draft["categories"]:
        bot.answer_callback_query(call.id, "Черновик не найден.")
        return

    with DATA_LOCK:
        review_counter += 1
        review_id = review_counter
        pending_review[review_id] = {
            "user_id": user_id,
            "text_html": draft["text_html"],
            "message_id": draft["message_id"],
            "media_group_messages": draft["media_group_messages"],
            "content_type": draft["content_type"],
            "categories": sorted(draft["categories"]),
            "tags": sorted(draft["tags"]),
            "admin_msg_id": None
        }
        review_user_id[review_id] = user_id
        user_drafts.pop(user_id, None)

    user_control_kb = types.InlineKeyboardMarkup()
    user_control_kb.row(
        types.InlineKeyboardButton("❌ Отменить тейк", callback_data=f"{CB_PREFIX_USER_CANCEL}{review_id}")
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
    all_tags = get_entry_categories(item) + item.get("tags", [])
    tags_line = "\n" + " ".join(f"#{t}" for t in all_tags)
    
    user_text = item.get("text_html", "").strip()
    preview_body = f"{user_text}{SIGNATURE_QUOTE}{tags_line}" if user_text else f"{SIGNATURE_QUOTE.strip()}{tags_line}"
    admin_text = f"📝 <b>Новый тейк на проверку (#{review_id})</b>\n\n{preview_body}"
    
    kb = build_admin_keyboard(review_id)

    if item.get("media_group_messages"):
        bot.copy_messages(ADMIN_CHAT_ID, user_id, item["media_group_messages"])
        admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_text, reply_markup=kb, parse_mode="HTML")
    elif item.get("content_type") == CONTENT_TYPE_TEXT:
        # admin_text уже содержит текст тейка + подпись + теги одним блоком
        admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_text, reply_markup=kb, parse_mode="HTML")
    else:
        admin_msg = bot.copy_message(
            ADMIN_CHAT_ID, user_id, item["message_id"],
            caption=admin_text, reply_markup=kb, parse_mode="HTML"
        )

    with DATA_LOCK:
        pending_review[review_id]["admin_msg_id"] = admin_msg.message_id
        admin_thread_messages[admin_msg.message_id] = review_id
        save_review_state()

# ---------- Общие хелперы для обработчиков модерации (убирают дублирование кода) ----------
def parse_review_id(call: types.CallbackQuery) -> int:
    return int(call.data.split(":", 1)[1])

def get_admin_display_name(user: types.User) -> str:
    return html.escape(user.first_name or user.username or "админ")

def clear_card_buttons_and_notify(call: types.CallbackQuery, text: str) -> None:
    """Убирает кнопки с карточки тейка у админов и пишет итог решения ответом на неё."""
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup())
    bot.send_message(call.message.chat.id, text, reply_to_message_id=call.message.message_id)

def pop_review_and_save(review_id: int) -> dict | None:
    with DATA_LOCK:
        item = pending_review.pop(review_id, None)
        save_review_state()
    return item

# ---------- Модерация и Бан ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_BAN))
def cb_ban_user(call: types.CallbackQuery) -> None:
    if not is_super_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Бан — только для главных админов.")
        return

    review_id = parse_review_id(call)
    item = pop_review_and_save(review_id)
    target_user_id = item["user_id"] if item else review_user_id.get(review_id)

    if not target_user_id:
        bot.answer_callback_query(call.id, "Пользователь не найден.")
        return

    if target_user_id not in BANNED_USERS:
        with DATA_LOCK:
            if target_user_id not in BANNED_USERS:  # повторная проверка внутри лока
                BANNED_USERS.append(target_user_id)
                save_banned(BANNED_USERS)

    kicked_from_chat = False
    try:
        bot.ban_chat_member(ADMIN_CHAT_ID, target_user_id)
        kicked_from_chat = True
    except ApiTelegramException as e:
        logging.warning(f"Не удалось исключить из КФ: {e}")

    chat_status = "и выбит из чата 🚪" if kicked_from_chat else "(не был в чате / нет прав)"
    clear_card_buttons_and_notify(
        call,
        f"🚫 <b>Пользователь {target_user_id} забанен {chat_status}</b> ({get_admin_display_name(call.from_user)})."
    )
    bot.answer_callback_query(call.id, "Забанен!")

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_APPROVE))
def cb_approve(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = parse_review_id(call)
    item = pop_review_and_save(review_id)
    if not item:
        bot.answer_callback_query(call.id, "Уже обработано.")
        return

    publish_time = schedule_add(item)
    time_str = publish_time.strftime("%d.%m %H:%M")

    bot.send_message(item["user_id"], f"✅ Ваш тейк одобрен! Публикация запланирована на {time_str} (мск).")
    clear_card_buttons_and_notify(
        call,
        f"✅ Принято ({get_admin_display_name(call.from_user)}), запланировано на {time_str} (мск)."
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_PUBLISH_NOW))
def cb_publish_now_review(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = parse_review_id(call)
    item = pop_review_and_save(review_id)
    if not item:
        bot.answer_callback_query(call.id, "Уже обработано.")
        return

    try:
        publish_entry(item)
        clear_card_buttons_and_notify(call, f"⚡️ Опубликовано мгновенно ({get_admin_display_name(call.from_user)}).")
        bot.answer_callback_query(call.id, "Опубликовано!")
    except (ApiTelegramException, RuntimeError) as e:
        logging.exception("Ошибка мгновенной публикации")
        bot.send_message(call.message.chat.id, f"⚠️ Ошибка при мгновенной публикации: {html.escape(str(e))}")
        bot.answer_callback_query(call.id, "Ошибка при публикации.")

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_REJECT))
def cb_reject(call: types.CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Только для админов.")
        return
    review_id = parse_review_id(call)
    item = pop_review_and_save(review_id)
    if not item:
        bot.answer_callback_query(call.id, "Уже обработано.")
        return
    bot.send_message(item["user_id"], "Ваш тейк отклонён за нарушение правил или по решению администрации.")
    clear_card_buttons_and_notify(call, f"❌ Отклонено ({get_admin_display_name(call.from_user)}).")
    bot.answer_callback_query(call.id)

if __name__ == "__main__":
    print("Bot started")

    def validate_required_chats() -> None:
        """Проверка при старте: бот действительно видит ADMIN_CHAT_ID, CHANNEL_ID и STORAGE_CHANNEL_ID.
        Если какой-то из них неверный/недоступный — сразу видно в логах Railway, не дожидаясь первой публикации."""
        checks = {
            "ADMIN_CHAT_ID": ADMIN_CHAT_ID,
            "CHANNEL_ID": CHANNEL_ID,
            "STORAGE_CHANNEL_ID": STORAGE_CHANNEL_ID,
        }
        problems = []
        for name, chat_id in checks.items():
            try:
                bot.get_chat(chat_id)
                logging.info(f"{name}={chat_id} — ок, бот видит этот чат")
            except ApiTelegramException as e:
                problems.append(f"{name}={chat_id}: {e}")
                logging.error(f"{name}={chat_id} — ОШИБКА: {e}")
        if problems:
            alert = "⚠️ При старте бота обнаружены проблемы с chat_id:\n\n" + "\n".join(problems)
            try:
                bot.send_message(ADMIN_CHAT_ID, alert)
            except ApiTelegramException:
                pass  # если сломан именно ADMIN_CHAT_ID — послать алерт туда же не получится, но в логах уже есть

    def handle_shutdown_signal(signum, frame) -> None:
        """SIGTERM/SIGINT (например, при редеплое на Railway) — останавливаем бота мягко,
        а не даём процессу оборваться посреди сетевого вызова или записи файла."""
        logging.info(f"Получен сигнал {signum}, останавливаюсь...")
        shutdown_event.set()
        bot.stop_polling()

    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    validate_required_chats()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    bot.infinity_polling()
    logging.info("Бот остановлен.")
    sys.exit(0)
