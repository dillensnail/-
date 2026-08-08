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
SUPER_ADMIN_IDS = [int(x) for x in os.environ.get("SUPER_ADMIN_IDS", "").split(",") if x.strip()] or ADMIN_IDS
ADMIN_CHAT_ID = os.environ["ADMIN_CHAT_ID"]  # чат/группа модераторов
CHANNEL_ID = os.environ["CHANNEL_ID"]        # публичный канал

# Технический чат/канал для анонимизации перед форвардом (по умолчанию ADMIN_CHAT_ID)
STORAGE_CHANNEL_ID = os.environ.get("STORAGE_CHANNEL_ID", ADMIN_CHAT_ID)

# Настройки подписи
COMMUNITY_HANDLE = os.environ.get("COMMUNITY_HANDLE", "@PsychoPromblem")
BOT_LINK = os.environ.get("BOT_LINK", "https://t.me/PsychoPromblembot")
SIGNATURE_QUOTE = f'\n\n<blockquote>#тейк | {COMMUNITY_HANDLE} | <a href="{BOT_LINK}">Send take now✧⁠*⁠。</a></blockquote>'

# ==============================================================================
# СИСТЕМА TW/CW
# ==============================================================================
TW_CW_TAGS = [
    "Селфхарм",
    "Суицид / попытки суицида",
    "Травля / буллинг",
    "Смерть животных / живодёрство",
    "Передозировка / медикаментозные срывы / плохая реакция на препараты",
    "РПП / переедание / излишнее похудение",
    "Наркозависимость",
    "Рвотные рефлексы / рвота / блевотина",
    "Изнасилование / упоминание насилия",
    "Расчленёнка / гуро"
]

def build_tw_cw_line(tw_cw_list: list) -> str:
    if not tw_cw_list:
        return ""
    sorted_tags = sorted(
        [t for t in tw_cw_list if t in TW_CW_TAGS],
        key=lambda x: TW_CW_TAGS.index(x)
    )
    if not sorted_tags:
        return ""
    return f"⚠️ <b>TW/CW:</b> {', '.join(sorted_tags)}\n\n"

RULES_URL = os.environ.get("RULES_URL", "https://t.me/your_rules_link")
CHANNEL_URL = os.environ.get("CHANNEL_URL", "")
CHANNEL_USERNAME_FOR_LINKS = os.environ.get("CHANNEL_USERNAME_FOR_LINKS", "MeshiMeshu")

CONTENT_TYPE_TEXT = "text"
CONTENT_TYPE_ALBUM = "альбом"

# Префиксы Callback-данных
CB_PREFIX_PUB_TYPE = "pubtype:"
CB_PREFIX_CATEGORY = "cat:"
CB_PREFIX_SYMPTOMS_ASK = "sym_ask:"
CB_PREFIX_TAG = "tag:"
CB_PREFIX_TWCW_ASK = "twcw_ask:"
CB_PREFIX_TWCW = "twcw:"
CB_PREFIX_GOTO = "goto:"
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

apihelper.RETRY_ON_ERROR = True
apihelper.RETRY_TIMEOUT = 5
apihelper.MAX_RETRIES = 3

DATA_LOCK = threading.RLock()

def atomic_write_json(path: str, data) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)

def get_entry_categories(entry: dict) -> list:
    if entry.get("categories"):
        return list(entry["categories"])
    if entry.get("category"):
        return [entry["category"]]
    return []

# Типы публикаций
PUB_TYPES = {
    "take": "Отправить тейк",
    "interactive": "Интерактив",
    "meme": "Мем",
    "useful": "Полезное",
    "other": "Другое"
}

CATEGORY_LABELS = {
    "диагнозы": "🧠 Диагнозы",
    "расстройства": "🌀 Расстройства",
    "нейроотличие": "🧩 Нейроотличия",
    "симптомы": "🤒 Симптомы и состояния",
    "жизнь": "💬 Другое",
}
CATEGORY_ORDER = ["диагнозы", "расстройства", "нейроотличие", "симптомы", "жизнь"]

HELP_RESOURCES_TEXT = (
    "Если тебе прямо сейчас тяжело — не оставайся с этим один/одна. Можно обратиться за помощью:\n\n"
    "🇷🇺 <b>Россия</b>\n☎️ Экстренная психологическая помощь: +7 (495) 989-50-50\n\n"
    "🇺АК <b>Україна</b>\n☎️ 112 / Lifeline Ukraine: 7333\n\n"
    "🇧🇾 <b>Беларусь</b>\n☎️ Экстренная психологическая помощь: 133\n\n"
    "🤖 @DBT_Skills_Bot — кнопка SOS"
)

# ---------- Забаненные ----------
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
        logging.error(f"Проверка подписки не удалась: {e}")
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
        pass
    return RULES_URL

def get_sub_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📢 Подписаться на канал", url=get_channel_url()))
    kb.add(types.InlineKeyboardButton("🔄 Я подписался", callback_data=CB_CHECK_SUB))
    return kb

# ---------- Хештеги ----------
MENTAL_HEALTH_TAGS = ["тревога", "паранойя", "срыв", "паническиеатаки", "диссоциация", "мелтдаун", "выгорание"]
NEURODIVERGENT_TAGS = ["сдвг", "рас", "дислексия", "гиперфокус", "специнтерес"]
DIAGNOSIS_TAGS = ["депрессия", "тревожноерасстройство", "бар", "шар", "прл", "птср", "кптср", "окр", "рпп", "дри", "шизофрения", "социофобия"]
LIFE_TAGS = ["интерактив", "другое", "мемы", "полезное"]

def load_tags() -> dict:
    default = {
        "диагнозы": DIAGNOSIS_TAGS,
        "расстройства": DIAGNOSIS_TAGS,
        "нейроотличие": NEURODIVERGENT_TAGS,
        "симптомы": MENTAL_HEALTH_TAGS,
        "жизнь": LIFE_TAGS,
    }
    if not os.path.exists(TAGS_FILE):
        save_tags(default)
        return default
    with open(TAGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_tags(tags: dict) -> None:
    atomic_write_json(TAGS_FILE, tags)

TAGS = load_tags()

CATEGORY_ALIASES = {
    "диагнозы": "диагнозы",
    "расстройства": "расстройства",
    "нейроотличие": "нейроотличие",
    "симптомы": "симптомы",
    "жизнь": "жизнь",
    "другое": "жизнь",
}

def load_list_file(path: str, default: list) -> list:
    if not os.path.exists(path):
        save_list_file(path, default)
        return list(default)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_list_file(path: str, data: list) -> None:
    atomic_write_json(path, data)

CAT_ARTS = load_list_file(CAT_ARTS_FILE, [])
SUPPORT_PHRASES = load_list_file(PHRASES_FILE, ["Ты не один.", "Спасибо за тейк. Помни: твои чувства важны."])

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
    full_caption = f"Спасибо за тейк!\n\n{phrase}" if phrase else "Спасибо за тейк!"
    try:
        if art_url:
            bot.send_photo(user_id, art_url, caption=full_caption)
        else:
            bot.send_message(user_id, full_caption)
    except ApiTelegramException:
        pass

def get_post_url(msg: types.Message) -> str:
    return f"https://t.me/{CHANNEL_USERNAME_FOR_LINKS}/{msg.message_id}"

def publish_entry(entry: dict) -> None:
    tags = entry.get("tags", [])
    all_tags = get_entry_categories(entry) + tags
    tags_line = "\n" + " ".join(f"#{t}" for t in all_tags)
    footer = f"{SIGNATURE_QUOTE.strip()}{tags_line}"
    tw_cw = build_tw_cw_line(entry.get("tw_cw", []))

    user_id = entry["user_id"]
    msg_id = entry.get("message_id")
    content_type = entry.get("content_type")

    final_msg = None

    if entry.get("media_group_messages"):
        if tw_cw:
            bot.send_message(CHANNEL_ID, tw_cw.strip(), parse_mode="HTML")
        stored_msgs = bot.copy_messages(STORAGE_CHANNEL_ID, user_id, entry["media_group_messages"])
        for m in stored_msgs:
            f_msg = bot.forward_message(CHANNEL_ID, STORAGE_CHANNEL_ID, m.message_id)
            if not final_msg:
                final_msg = f_msg
        bot.send_message(CHANNEL_ID, footer, parse_mode="HTML")

    elif content_type == CONTENT_TYPE_TEXT:
        original_text = entry.get("text_html", "").strip()
        full_text = f"{tw_cw}{original_text}{footer}" if original_text else f"{tw_cw}{footer}"
        final_msg = bot.send_message(CHANNEL_ID, full_text, parse_mode="HTML")

    else:
        original_text = entry.get("text_html", "").strip()
        full_text = f"{tw_cw}{original_text}{footer}" if original_text else f"{tw_cw}{footer}"
        stored_msg = bot.copy_message(STORAGE_CHANNEL_ID, user_id, msg_id, caption=full_text, parse_mode="HTML")
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
        except ApiTelegramException:
            pass

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
                    except Exception as e:
                        logging.exception("Ошибка планировщика")
                if changed:
                    save_schedule(SCHEDULE)
        except Exception:
            logging.exception("Ошибка в цикле планировщика")
        shutdown_event.wait(20)

# ---------- Состояние модерации ----------
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
        "🌸 <b>Добро пожаловать!</b>\n\nПросто отправьте текст или медиафайл сюда, чтобы начать подготовку публикации.",
        reply_markup=kb,
    )

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

# ---------- Приём сообщений и инициализация черновика ----------
def init_draft(user_id: int, text_html: str, message_id: int, media_group_messages: list, content_type: str) -> None:
    user_drafts[user_id] = {
        "text_html": text_html,
        "message_id": message_id,
        "media_group_messages": media_group_messages,
        "content_type": content_type,
        "pub_type": None,
        "categories": set(),
        "symptoms": set(),
        "tags": set(),
        "tw_cw": set(),
        "step": "type_select",  # Стартовый шаг
        "help_message_sent": False
    }

def process_media_group(mg_id: str) -> None:
    with media_groups_lock:
        data = media_groups_buffer.pop(mg_id, None)
    if not data or is_banned(data["user_id"]):
        return
    init_draft(data["user_id"], data["caption_html"], data["messages"][0], data["messages"], CONTENT_TYPE_ALBUM)
    send_wizard_step(data["user_id"])

@bot.message_handler(
    content_types=["text", "photo", "video", "document", "animation", "audio", "voice", "video_note"],
    func=lambda m: m.chat.type == "private" and not (m.reply_to_message and m.reply_to_message.message_id in user_thread_messages)
)
def handle_incoming_take(message: types.Message) -> None:
    if message.text and message.text.startswith("/"):
        return
    user_id = message.from_user.id
    if is_banned(user_id):
        bot.send_message(user_id, "❌ Вы заблокированы.")
        return
    if not check_subscription(user_id):
        bot.send_message(user_id, "⚠️ <b>Для отправки тейка нужно подписаться на канал!</b>", reply_markup=get_sub_keyboard())
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
    init_draft(user_id, text_html, message.message_id, None, message.content_type)
    send_wizard_step(user_id)

# ==============================================================================
# ЕДИНЫЙ FLOW (ТИП -> КАТЕГОРИЯ -> СИМПТОМЫ -> TW/CW -> ХЕШТЕГИ -> ПРЕДПРОСМОР)
# ==============================================================================
def render_preview_text(draft: dict) -> str:
    tw_cw_line = build_tw_cw_line(list(draft.get("tw_cw", [])))
    text = draft.get("text_html", "").strip()
    
    all_tags = list(draft.get("categories", set())) + list(draft.get("tags", set())) + list(draft.get("symptoms", set()))
    tags_line = "\n" + " ".join(f"#{t}" for t in all_tags) if all_tags else ""
    
    pub_type_title = PUB_TYPES.get(draft.get("pub_type"), "Тейк")
    
    header = f"<b>👁 Предпросмотр ({pub_type_title}):</b>\n\n"
    content = f"{tw_cw_line}{text}{SIGNATURE_QUOTE}{tags_line}"
    return f"{header}{content}"

def build_wizard_keyboard(draft: dict) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    step = draft.get("step")

    if step == "type_select":
        kb.add(types.InlineKeyboardButton("1. Отправить тейк", callback_data=f"{CB_PREFIX_PUB_TYPE}take"))
        kb.add(types.InlineKeyboardButton("2. Интерактив", callback_data=f"{CB_PREFIX_PUB_TYPE}interactive"))
        kb.add(types.InlineKeyboardButton("3. Мем", callback_data=f"{CB_PREFIX_PUB_TYPE}meme"))
        kb.add(types.InlineKeyboardButton("4. Полезное", callback_data=f"{CB_PREFIX_PUB_TYPE}useful"))
        kb.add(types.InlineKeyboardButton("5. Другое", callback_data=f"{CB_PREFIX_PUB_TYPE}other"))
        kb.row(types.InlineKeyboardButton("❌ Отменить", callback_data=CB_CANCEL))

    elif step == "category_select":
        for key in CATEGORY_ORDER:
            prefix = "✅ " if key in draft["categories"] else ""
            kb.add(types.InlineKeyboardButton(f"{prefix}{CATEGORY_LABELS[key]}", callback_data=f"{CB_PREFIX_CATEGORY}{key}"))
        if draft["categories"]:
            kb.row(types.InlineKeyboardButton("➡️ Далее", callback_data=f"{CB_PREFIX_GOTO}symptoms_ask"))
        kb.row(types.InlineKeyboardButton("❌ Отменить", callback_data=CB_CANCEL))

    elif step == "symptoms_ask":
        kb.row(
            types.InlineKeyboardButton("✅ Да", callback_data=f"{CB_PREFIX_SYMPTOMS_ASK}yes"),
            types.InlineKeyboardButton("❌ Нет", callback_data=f"{CB_PREFIX_SYMPTOMS_ASK}no")
        )
        kb.row(types.InlineKeyboardButton("❌ Отменить", callback_data=CB_CANCEL))

    elif step == "symptoms_select":
        for category in draft["categories"]:
            for tag in TAGS.get(category, []):
                prefix = "✅ " if tag in draft["symptoms"] else ""
                kb.add(types.InlineKeyboardButton(f"{prefix}#{tag}", callback_data=f"{CB_PREFIX_TAG}sym:{tag}"))
        kb.row(types.InlineKeyboardButton("➡️ Далее к TW/CW", callback_data=f"{CB_PREFIX_GOTO}twcw_ask"))

    elif step == "twcw_ask":
        kb.row(
            types.InlineKeyboardButton("✅ Да", callback_data=f"{CB_PREFIX_TWCW_ASK}yes"),
            types.InlineKeyboardButton("❌ Нет", callback_data=f"{CB_PREFIX_TWCW_ASK}no")
        )
        kb.row(types.InlineKeyboardButton("❌ Отменить", callback_data=CB_CANCEL))

    elif step == "twcw_select":
        for idx, tw_item in enumerate(TW_CW_TAGS):
            prefix = "☑️ " if tw_item in draft.get("tw_cw", set()) else "🔲 "
            kb.row(types.InlineKeyboardButton(f"{prefix}{tw_item}", callback_data=f"{CB_PREFIX_TWCW}{idx}"))
        kb.row(types.InlineKeyboardButton("➡️ Далее к хештегам", callback_data=f"{CB_PREFIX_GOTO}hashtags_menu"))

    elif step == "hashtags_menu":
        for cat, tag_list in TAGS.items():
            for t in tag_list[:4]:  # Дополнительные теги
                prefix = "✅ " if t in draft["tags"] else ""
                kb.add(types.InlineKeyboardButton(f"{prefix}#{t}", callback_data=f"{CB_PREFIX_TAG}extra:{t}"))
        kb.row(types.InlineKeyboardButton("➡️ К предпросмотру", callback_data=f"{CB_PREFIX_GOTO}preview"))
        kb.row(types.InlineKeyboardButton("❌ Отменить", callback_data=CB_CANCEL))

    elif step == "preview":
        kb.row(types.InlineKeyboardButton("✅ Отправить тейк", callback_data=CB_CONFIRM))
        kb.row(types.InlineKeyboardButton("✏️ Изменить", callback_data=f"{CB_PREFIX_GOTO}type_select"))
        kb.row(types.InlineKeyboardButton("❌ Отменить", callback_data=CB_CANCEL))

    return kb

def get_wizard_message_text(draft: dict) -> str:
    step = draft.get("step")
    if step == "type_select":
        return "<b>Шаг 1.</b> Выберите тип публикации:"
    elif step == "category_select":
        return "<b>Шаг 2.</b> Выберите основную категорию:"
    elif step == "symptoms_ask":
        return "<b>Шаг 3.</b> Добавить симптомы/особенности?"
    elif step == "symptoms_select":
        return "<b>Шаг 3.1.</b> Отметьте симптомы и особенности:"
    elif step == "twcw_ask":
        return "<b>Шаг 4.</b> Добавить TW/CW (предупреждения о чувствительном контенте)?"
    elif step == "twcw_select":
        return "<b>Шаг 4.1.</b> Выберите чувствительные темы (TW/CW):"
    elif step == "hashtags_menu":
        return "<b>Шаг 5.</b> Меню подготовки публикации. Вы можете добавить хештеги:"
    elif step == "preview":
        return render_preview_text(draft)
    return "Выберите действие:"

def send_wizard_step(user_id: int, message_id: int = None) -> None:
    draft = user_drafts.get(user_id)
    if not draft:
        return
    text = get_wizard_message_text(draft)
    kb = build_wizard_keyboard(draft)
    if message_id:
        try:
            bot.edit_message_text(text, user_id, message_id, reply_markup=kb, parse_mode="HTML")
            return
        except ApiTelegramException:
            pass
    bot.send_message(user_id, text, reply_markup=kb, parse_mode="HTML")

# ---------- Обработчики переходов единого Flow ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_PUB_TYPE))
def cb_select_pub_type(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft:
        bot.answer_callback_query(call.id, "Сессия истекла. Отправьте тейк заново.")
        return

    pub_type = call.data.split(":", 1)[1]
    draft["pub_type"] = pub_type

    if pub_type == "other":
        draft["categories"] = {"жизнь"}
        draft["step"] = "hashtags_menu"  # Ветка "Другое" переходит сразу к подтоговке и хештегам
    else:
        draft["step"] = "category_select"

    send_wizard_step(user_id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_CATEGORY))
def cb_select_category(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft:
        bot.answer_callback_query(call.id)
        return
    
    cat = call.data.split(":", 1)[1]
    if cat in draft["categories"]:
        draft["categories"].remove(cat)
    else:
        draft["categories"].add(cat)

    send_wizard_step(user_id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_SYMPTOMS_ASK))
def cb_symptoms_ask(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft:
        return
    ans = call.data.split(":", 1)[1]
    if ans == "yes":
        draft["step"] = "symptoms_select"
    else:
        draft["step"] = "twcw_ask"
    send_wizard_step(user_id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_TWCW_ASK))
def cb_twcw_ask(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft:
        return
    ans = call.data.split(":", 1)[1]
    if ans == "yes":
        draft["step"] = "twcw_select"
    else:
        draft["step"] = "hashtags_menu"
    send_wizard_step(user_id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_TWCW))
def cb_toggle_twcw(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft:
        return
    idx = int(call.data.split(":", 1)[1])
    if 0 <= idx < len(TW_CW_TAGS):
        item = TW_CW_TAGS[idx]
        if item in draft["tw_cw"]:
            draft["tw_cw"].remove(item)
        else:
            draft["tw_cw"].add(item)
            if not draft.get("help_message_sent"):
                draft["help_message_sent"] = True
                try:
                    bot.send_message(user_id, HELP_RESOURCES_TEXT, parse_mode="HTML", disable_web_page_preview=True)
                except ApiTelegramException:
                    pass
    send_wizard_step(user_id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_TAG))
def cb_toggle_tag(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft:
        return
    payload = call.data.split(":", 1)[1]
    tag_type, tag_val = payload.split(":", 1)
    
    target_set = draft["symptoms"] if tag_type == "sym" else draft["tags"]
    if tag_val in target_set:
        target_set.remove(tag_val)
    else:
        target_set.add(tag_val)

    send_wizard_step(user_id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_GOTO))
def cb_goto_step(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    draft = user_drafts.get(user_id)
    if not draft:
        return
    target_step = call.data.split(":", 1)[1]
    draft["step"] = target_step
    send_wizard_step(user_id, call.message.message_id)
    bot.answer_callback_query(call.id)

# ---------- Финальное подтверждение публикации ----------
@bot.callback_query_handler(func=lambda c: c.data == CB_CONFIRM)
def cb_confirm(call: types.CallbackQuery) -> None:
    global review_counter
    user_id = call.from_user.id

    if not check_subscription(user_id):
        bot.send_message(user_id, "⚠️ Вы не подписаны на канал!", reply_markup=get_sub_keyboard())
        bot.answer_callback_query(call.id)
        return

    draft = user_drafts.get(user_id)
    if not draft or not draft.get("categories"):
        bot.answer_callback_query(call.id, "Категория не выбрана!", show_alert=True)
        return

    with DATA_LOCK:
        review_counter += 1
        review_id = review_counter
        all_selected_tags = list(draft["tags"]) + list(draft["symptoms"])
        pending_review[review_id] = {
            "user_id": user_id,
            "text_html": draft["text_html"],
            "message_id": draft["message_id"],
            "media_group_messages": draft["media_group_messages"],
            "content_type": draft["content_type"],
            "categories": sorted(draft["categories"]),
            "tags": sorted(all_selected_tags),
            "tw_cw": list(draft.get("tw_cw", set())),
            "admin_msg_id": None
        }
        review_user_id[review_id] = user_id
        user_drafts.pop(user_id, None)

    user_control_kb = types.InlineKeyboardMarkup()
    user_control_kb.row(
        types.InlineKeyboardButton("❌ Отменить тейк", callback_data=f"{CB_PREFIX_USER_CANCEL}{review_id}")
    )

    bot.edit_message_text(
        "✨ Ваш тейк отправлен на модерацию!\n<i>(Если захотите изменить текст — просто отредактируйте исходное сообщение)</i>",
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
    tw_cw_header = build_tw_cw_line(item.get("tw_cw", []))
    user_text = item.get("text_html", "").strip()

    body = f"{tw_cw_header}{user_text}" if user_text else tw_cw_header.strip()
    preview_body = f"{body}{SIGNATURE_QUOTE}{tags_line}"
    admin_text = f"📝 <b>Новый тейк на проверку (#{review_id})</b>\n\n{preview_body}"
    kb = build_admin_keyboard(review_id)

    if item.get("media_group_messages"):
        bot.copy_messages(ADMIN_CHAT_ID, user_id, item["media_group_messages"])
        admin_msg = bot.send_message(ADMIN_CHAT_ID, admin_text, reply_markup=kb, parse_mode="HTML")
    elif item.get("content_type") == CONTENT_TYPE_TEXT:
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

# ---------- Отмена тейка автором ----------
@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_USER_CANCEL))
def cb_user_cancel(call: types.CallbackQuery) -> None:
    review_id = int(call.data.split(":")[1])
    item = pop_review_and_save(review_id)
    if not item:
        bot.answer_callback_query(call.id, "Тейк уже обработан!", show_alert=True)
        return

    admin_msg_id = item.get("admin_msg_id")
    if admin_msg_id:
        try:
            bot.edit_message_text(f"❌ <b>Тейк #{review_id} отменён автором.</b>", ADMIN_CHAT_ID, admin_msg_id)
        except ApiTelegramException:
            pass

    bot.edit_message_text("❌ Ваш тейк отменён.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, "Отменено.")

@bot.callback_query_handler(func=lambda c: c.data == CB_CANCEL)
def cb_cancel(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    user_drafts.pop(user_id, None)
    bot.edit_message_text("Отменено.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

# ---------- Модерация и Административные функции ----------
def parse_review_id(call: types.CallbackQuery) -> int:
    return int(call.data.split(":", 1)[1])

def get_admin_display_name(user: types.User) -> str:
    return html.escape(user.first_name or user.username or "админ")

def clear_card_buttons_and_notify(call: types.CallbackQuery, text: str) -> None:
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup())
    bot.send_message(call.message.chat.id, text, reply_to_message_id=call.message.message_id)

def pop_review_and_save(review_id: int) -> dict | None:
    with DATA_LOCK:
        item = pending_review.pop(review_id, None)
        save_review_state()
    return item

@bot.callback_query_handler(func=lambda c: c.data.startswith(CB_PREFIX_BAN))
def cb_ban_user(call: types.CallbackQuery) -> None:
    if not is_super_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Бан доступен только главным админам.")
        return
    review_id = parse_review_id(call)
    item = pop_review_and_save(review_id)
    target_user_id = item["user_id"] if item else review_user_id.get(review_id)

    if not target_user_id:
        bot.answer_callback_query(call.id, "Пользователь не найден.")
        return

    if target_user_id not in BANNED_USERS:
        with DATA_LOCK:
            if target_user_id not in BANNED_USERS:
                BANNED_USERS.append(target_user_id)
                save_banned(BANNED_USERS)

    clear_card_buttons_and_notify(call, f"🚫 <b>Пользователь {target_user_id} забанен</b> ({get_admin_display_name(call.from_user)}).")
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
    clear_card_buttons_and_notify(call, f"✅ Принято ({get_admin_display_name(call.from_user)}), запланировано на {time_str} (мск).")
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
    except Exception as e:
        bot.send_message(call.message.chat.id, f"⚠️ Ошибка публикации: {html.escape(str(e))}")
        bot.answer_callback_query(call.id, "Ошибка.")

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
    bot.send_message(item["user_id"], "Ваш тейк отклонён администрацией.")
    clear_card_buttons_and_notify(call, f"❌ Отклонено ({get_admin_display_name(call.from_user)}).")
    bot.answer_callback_query(call.id)

if __name__ == "__main__":
    print("Bot started")

    def handle_shutdown_signal(signum, frame) -> None:
        shutdown_event.set()
        bot.stop_polling()

    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    threading.Thread(target=scheduler_loop, daemon=True).start()
    bot.infinity_polling()
    sys.exit(0)
