import asyncio
import os

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from storage import Storage
from xui import XUIClient


def parse_admin_ids(raw: str) -> set[int]:
    if not raw:
        return set()
    result: set[int] = set()
    for x in raw.split(","):
        x = x.strip()
        if x.isdigit():
            result.add(int(x))
    return result


load_dotenv()

# --- ENV ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

XUI_BASE_URL = os.getenv("XUI_BASE_URL", "").strip()
XUI_WEBBASEPATH = os.getenv("XUI_WEBBASEPATH", "").strip()  # например "/EmptyArclight_panel" или ""
XUI_API_PREFIX = os.getenv("XUI_API_PREFIX", "/panel/api").strip()  # "/panel/api" или "/api"
XUI_USERNAME = os.getenv("XUI_USERNAME", "").strip()
XUI_PASSWORD = os.getenv("XUI_PASSWORD", "").strip()

# В актуальном 3x-ui у клиента есть штатное поле tgId
XUI_TGID_FIELD = os.getenv("XUI_TGID_FIELD", "tgId").strip()

# online  = прямо сейчас онлайн (onlines)
# enabled = включён и не просрочен
XUI_ACTIVE_MODE = os.getenv("XUI_ACTIVE_MODE", "enabled").strip().lower()

# Если TLS self-signed: XUI_VERIFY_TLS=false
XUI_VERIFY_TLS = os.getenv("XUI_VERIFY_TLS", "true").strip().lower() in {"1", "true", "yes", "y"}

ADMIN_IDS = parse_admin_ids(os.getenv("ADMIN_IDS", ""))
DB_PATH = os.getenv("DB_PATH", "users.db").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty in .env")

if XUI_ACTIVE_MODE not in {"enabled", "online"}:
    raise RuntimeError("XUI_ACTIVE_MODE must be 'enabled' or 'online'")

# --- BOT ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

storage = Storage(DB_PATH)

xui = XUIClient(
    base_url=XUI_BASE_URL,
    username=XUI_USERNAME,
    password=XUI_PASSWORD,
    tg_field=XUI_TGID_FIELD,
    active_mode=XUI_ACTIVE_MODE,
    web_basepath=XUI_WEBBASEPATH,
    api_prefix=XUI_API_PREFIX,
    verify_tls=XUI_VERIFY_TLS,
)


def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS


def xui_config_ok() -> bool:
    return bool(XUI_BASE_URL and XUI_USERNAME and XUI_PASSWORD)


@dp.message(Command("start"))
async def start(message: Message):
    tg_id = message.from_user.id
    storage.upsert_user(tg_id)

    text = (
        "Привет! Я бот для работы с 3x-ui.\n\n"
        f"Ваш Telegram ID: {tg_id}\n\n"
        "Команды:\n"
        "  /id — показать ваш TG ID\n"
        "  /active — активные клиенты по вашему TG ID\n"
        "  /whoami — показать роль\n"
    )

    if is_admin(tg_id):
        text += (
            "\nАдмин-команды:\n"
            "  /users — сколько пользователей у бота\n"
            "  /broadcast <текст> — рассылка всем пользователям\n"
        )

    await message.answer(text)


@dp.message(Command("id"))
async def cmd_id(message: Message):
    tg_id = message.from_user.id
    storage.upsert_user(tg_id)
    await message.answer(f"Ваш Telegram ID: {tg_id}")


@dp.message(Command("whoami"))
async def cmd_whoami(message: Message):
    tg_id = message.from_user.id
    storage.upsert_user(tg_id)
    role = "админ" if is_admin(tg_id) else "пользователь"
    await message.answer(f"TG ID: {tg_id}\nРоль: {role}")


@dp.message(Command("users"))
async def cmd_users(message: Message):
    tg_id = message.from_user.id
    storage.upsert_user(tg_id)

    if not is_admin(tg_id):
        await message.answer("У вас нет прав администратора.")
        return

    await message.answer(f"Пользователей в базе: {storage.count_users()}")


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    tg_id = message.from_user.id
    storage.upsert_user(tg_id)

    if not is_admin(tg_id):
        await message.answer("У вас нет прав администратора.")
        return

    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Использование: /broadcast <сообщение>")
        return

    msg = parts[1].strip()
    users = storage.list_users()

    sent = 0
    failed = 0

    for uid in users:
        try:
            await bot.send_message(uid, msg)
            sent += 1
        except Exception:
            failed += 1

    await message.answer(f"Рассылка завершена.\nОтправлено: {sent}\nОшибок: {failed}")


@dp.message(Command("active"))
async def cmd_active(message: Message):
    tg_id = message.from_user.id
    storage.upsert_user(tg_id)

    if not xui_config_ok():
        await message.answer("XUI_* переменные не заполнены в .env (BASE_URL/USERNAME/PASSWORD).")
        return

    try:
        clients = await xui.get_active_clients_for_tg(tg_id)
    except Exception as e:
        debug = (
            f"base_url: {XUI_BASE_URL}\n"
            f"web_basepath: {XUI_WEBBASEPATH or '(empty)'}\n"
            f"api_prefix: {XUI_API_PREFIX}\n"
            f"active_mode: {XUI_ACTIVE_MODE}\n"
            f"tg_field: {XUI_TGID_FIELD}\n"
            f"verify_tls: {XUI_VERIFY_TLS}"
        )
        await message.answer(f"Ошибка запроса к 3x-ui: {type(e).__name__}: {e}\n\n{debug}")
        return

    if not clients:
        await message.answer(
            "Активные клиенты не найдены.\n\n"
            "Проверьте, что у клиента в 3x-ui заполнено поле Telegram ID (tgId) вашим TG ID.\n"
            f"Сейчас бот ищет по полю: {XUI_TGID_FIELD}\n"
            f"Режим активных: {XUI_ACTIVE_MODE}"
        )
        return

    lines = [f"Найдены активные клиенты (режим: {XUI_ACTIVE_MODE}):"]
    for item in clients[:30]:
        c = item["client"]
        email = c.get("email") or c.get("remark") or "(no email/remark)"
        uuid = c.get("id") or c.get("uuid") or "(no id)"
        tgId = c.get("tgId", "(no tgId)")
        enabled = c.get("enable")
        exp = c.get("expiryTime")
        lines.append(
            f"• inbound #{item['inbound_id']} ({item.get('protocol')}:{item.get('port')})\n"
            f"  tgId: {tgId}\n"
            f"  email/remark: {email}\n"
            f"  id/uuid: {uuid}\n"
            f"  enable: {enabled}, expiryTime: {exp}"
        )

    if len(clients) > 30:
        lines.append(f"\nПоказано 30 из {len(clients)}.")

    await message.answer("\n".join(lines))


async def main():
    # Пробуем логин при старте — если не получится, бот всё равно запустится.
    try:
        if xui_config_ok():
            await xui.login()
    except Exception:
        pass

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
