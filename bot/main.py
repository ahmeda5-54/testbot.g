import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

import config
import db
from bot import bridge, texts
from bot.handlers_admin import router as admin_router
from bot.handlers_start import router as start_router
from bot.handlers_support import router as support_router
from bot.handlers_support_pointer import router as support_pointer_router
from bot.handlers_support_start import router as support_start_router
from bot.handlers_subscribe import router as subscribe_router
from bot.handlers_verify import router as verify_router

SUPPORT_ROUTERS = [
    support_router,
    subscribe_router,
    support_start_router,
]


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


async def _poll_and_recover(dispatcher: Dispatcher, bot: Bot) -> None:
    """يشغّل البولينغ ويعيد تشغيله عند السقوط، ويوقف عند طلب إعادة تشغيل."""
    while True:
        if config.RESTART_FLAG.is_file():
            try:
                config.RESTART_FLAG.unlink()
            except OSError:
                pass
            print("[bot] restart requested — rebooting with new settings", flush=True)
            return
        try:
            await dispatcher.start_polling(bot, polling_timeout=3)
        except Exception as exc:
            print(f"[bot] polling crashed: {exc!r}, restarting in 5s", flush=True)
            await asyncio.sleep(5)


async def run() -> None:
    db.init_db()
    bot = Bot(config.BOT_TOKEN, proxy=config.PROXY_URL)
    dp = Dispatcher()
    dp.include_router(start_router)
    dp.include_router(verify_router)
    dp.include_router(admin_router)

    # وضع تشغيل بوت الدعم — تحديد واضح أيهما يعمل للدعم
    if config.SUPPORT_MODE == "inline":
        use_dedicated = False
    elif config.SUPPORT_MODE == "dedicated":
        use_dedicated = bool(config.SUPPORT_BOT_TOKEN)
    else:  # auto
        use_dedicated = bool(config.SUPPORT_BOT_TOKEN)

    if use_dedicated:
        # البوت الرئيسي = كل المهام عدا الدعم (يوجّه المستخدم لبوت الدعم فقط)
        dp.include_router(support_pointer_router)
    else:
        # البوت الرئيسي = كل المهام بما فيها الدعم
        dp.include_router(support_router)

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

    pollers = [_poll_and_recover(dp, bot)]

    if use_dedicated and config.SUPPORT_BOT_TOKEN:
        support_bot = Bot(config.SUPPORT_BOT_TOKEN, proxy=config.PROXY_URL)
        sdp = Dispatcher()
        for router in SUPPORT_ROUTERS:
            sdp.include_router(router)
        bridge.register_support_bot(support_bot)
        try:
            await support_bot.set_my_commands(
                [
                    BotCommand(command="start", description="فتح خدمة الدعم"),
                    BotCommand(command="support", description="فتح خدمة الدعم"),
                ]
            )
        except Exception:
            pass
        try:
            await support_bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        pollers.append(_poll_and_recover(sdp, support_bot))
        print("[bot] dedicated support bot active", flush=True)
    else:
        # لا بوت دعم — رسائل الدعم تمر عبر البوت الرئيسي حصرياً
        bridge.register_support_bot(None)
        print(f"[bot] support inline (mode={config.SUPPORT_MODE})", flush=True)

    await asyncio.gather(*pollers)
