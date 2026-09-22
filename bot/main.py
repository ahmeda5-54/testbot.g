import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

import config
import db
from bot import bridge, texts
from bot.handlers_admin import router as admin_router
from bot.handlers_start import router as start_router
from bot.handlers_support import router as support_router
from bot.handlers_verify import router as verify_router


async def _expiry_loop(bot: Bot) -> None:
    while True:
        try:
            expired = db.expire_overdue()
            auto_remove = db.get_setting("auto_remove_expired", "0") == "1"
            for member in expired:
                removed = False
                if auto_remove:
                    ok, _err = await bridge._remove_member(member["telegramUserId"])
                    if ok:
                        db.set_status(member["telegramUserId"], db.REMOVED)
                        removed = True
                message = texts.admin_expired(member)
                if removed:
                    message += "\n\n⛔ تم حذف العضو من المجموعة تلقائياً."
                bridge.notify_admins(message, config.ADMIN_IDS)
        except Exception:
            pass
        await asyncio.sleep(60)


async def _heartbeat_loop(bot: Bot) -> None:
    while True:
        try:
            await bot.get_me()
            db.set_setting("bot_online", "1")
            db.set_setting("bot_last_seen", db.now_iso())
        except Exception:
            db.set_setting("bot_online", "0")
        await asyncio.sleep(30)


async def _initial_membership_check() -> None:
    await asyncio.sleep(5)
    try:
        await bridge.check_all_members_task()
    except Exception as exc:
        print(f"[bot] initial membership check failed: {exc!r}", flush=True)


async def run() -> None:
    db.init_db()
    bot = Bot(config.BOT_TOKEN, proxy=config.PROXY_URL)
    dp = Dispatcher()
    dp.include_router(start_router)
    dp.include_router(support_router)
    dp.include_router(verify_router)
    dp.include_router(admin_router)

    bridge.register(asyncio.get_running_loop(), bot)
    asyncio.create_task(_expiry_loop(bot))
    asyncio.create_task(_initial_membership_check())
    asyncio.create_task(_heartbeat_loop(bot))

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="ابدأ التحقق"),
                BotCommand(command="support", description="تواصل مع الدعم"),
                BotCommand(command="post", description="نشر رسالة القناة"),
            ]
        )
    except Exception:
        pass

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass

    while True:
        try:
            await dp.start_polling(bot, polling_timeout=3)
        except Exception as exc:
            print(f"[bot] polling crashed: {exc!r}, restarting in 5s")
            await asyncio.sleep(5)
