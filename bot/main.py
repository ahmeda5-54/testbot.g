import asyncio
import importlib

from aiogram import Bot, Dispatcher, Router
from aiogram.types import BotCommand

import config
import db
from bot import bridge, texts

# الوحدات تُستورد مرة وتُعاد تحميلها داخل run() لبناء Routers جديدة كل دورة
import bot.handlers_admin
import bot.handlers_start
import bot.handlers_support
import bot.handlers_support_pointer
import bot.handlers_support_start
import bot.handlers_subscribe
import bot.handlers_verify


def _fresh_router(module) -> Router:
    """يعيد تحميل وحدة المعالجات لتنشئ Router جديداً بالكامل.

    aiogram يرفض ربط نفس كائن Router بديسباتشر جديد، وrewire المبني على
    إعادة تشغيل run() يحتاج ديسباتشرات جديدة دائماً، لذا يجب أن يكون كل
    Router طازجاً في كل دورة. reload يعيد تنفيذ الوحدة فيُسجَّل الـ Router
    الجديد بحالاته المناسبة (الوحدات التابعة flow/texts/db لا تُعاد تحميلها)."""
    return importlib.reload(module).router


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


async def _restart_watcher(dispatcher: Dispatcher) -> None:
    """ينتظر علم إعادة التشغيل (من لوحة الإعدادات)، ثم يوقف polling بسلاسة.

    stop_polling() يرمي RuntimeError لو لم تكن polling قد بدأت، لذا ننتظر
    حتى يمسك الـ dispatcher بأنه يعمل فعلاً (نفس حراسة aiogram الداخلية).
    بعد عودة start_polling بسبب هذا الإيقاف، يعود run() ويعيد بناء كل شيء
    بقيم الإعدادات الجديدة. العلم يُحذف هنا لئلا نتكرر في إعادة تشغيل لا نهائية.
    """
    while not config.RESTART_FLAG.is_file():
        await asyncio.sleep(1)
    try:
        config.RESTART_FLAG.unlink()
    except OSError:
        pass
    print("[bot] restart flag detected — stopping polling to rewire", flush=True)
    # ننتظر حتى بدء polling فعلياً (الـ dispatcher يمسك قفل التشغيل)
    while True:
        running_lock = getattr(dispatcher, "_running_lock", None)
        if running_lock is None or not running_lock.locked():
            await asyncio.sleep(0.2)
            continue
        try:
            await dispatcher.stop_polling()
            return
        except RuntimeError:
            await asyncio.sleep(0.5)


async def _poll_and_recover(dispatcher: Dispatcher, bot: Bot) -> None:
    """يشغّل polling للبوت ويعيد التشغيل عند السقوط. watcher واحد يبقى حيّاً
    طوال الجلسة: لو التُقط علم إعادة التشغيل ثم سقط polling لاحقاً، يطبّق
    الـ watcher الإيقاف عند أول إقلاع ناجح؛ ولا يُلغى إلا عند المغادرة بسلاسة."""
    watcher = asyncio.create_task(_restart_watcher(dispatcher))
    while True:
        try:
            await dispatcher.start_polling(bot, polling_timeout=3)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[bot] polling crashed: {exc!r}, restarting in 5s", flush=True)
            await asyncio.sleep(5)
            continue
        if not watcher.done():
            watcher.cancel()
        # عاد start_polling بسلاسة = أُوقف بسبب علم إعادة التشغيل → نعيد البناء
        print("[bot] polling stopped normally — rewiring with latest settings", flush=True)
        return


async def _set_commands(bot: Bot, commands: list[tuple[str, str]]) -> None:
    try:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in commands])
    except Exception:
        pass


async def _delete_webhook(bot: Bot) -> None:
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass


async def run() -> None:
    db.init_db()
    bot = Bot(config.BOT_TOKEN, proxy=config.PROXY_URL)
    dp = Dispatcher()

    # Routers جديدة في كل دورة — ترتيب reload مهم: handlers_subscribe يستورد
    # دوال التحقق من handlers_verify، لذا يُعاد تحميل verify أولاً.
    start_router = _fresh_router(bot.handlers_start)
    verify_router = _fresh_router(bot.handlers_verify)
    admin_router = _fresh_router(bot.handlers_admin)
    support_router = _fresh_router(bot.handlers_support)
    subscribe_router = _fresh_router(bot.handlers_subscribe)
    support_start_router = _fresh_router(bot.handlers_support_start)
    support_pointer_router = _fresh_router(bot.handlers_support_pointer)

    dp.include_router(start_router)
    dp.include_router(verify_router)
    dp.include_router(admin_router)

    # وضع تشغيل بوت الدعم — تحديد واضح أيهما يعمل للدعم
    use_dedicated = config.SUPPORT_DEDICATED

    if use_dedicated:
        # البوت الرئيسي = كل المهام عدا الدعم (يوجّه المستخدم لبوت الدعم فقط)
        dp.include_router(support_pointer_router)
    else:
        # البوت الرئيسي = كل المهام بما فيها الدعم والاشتراك
        dp.include_router(support_router)
        dp.include_router(subscribe_router)

    bridge.register(asyncio.get_running_loop(), bot)
    tasks = [
        asyncio.create_task(_expiry_loop(bot)),
        asyncio.create_task(_initial_membership_check()),
        asyncio.create_task(_heartbeat_loop(bot)),
    ]

    await _set_commands(
        bot,
        [
            ("start", "ابدأ التحقق"),
            ("support", "تواصل مع الدعم"),
            ("post", "نشر رسالة القناة"),
        ],
    )
    await _delete_webhook(bot)

    pollers = [_poll_and_recover(dp, bot)]
    support_bot = None
    if use_dedicated and config.SUPPORT_BOT_TOKEN:
        support_bot = Bot(config.SUPPORT_BOT_TOKEN, proxy=config.PROXY_URL)
        sdp = Dispatcher()
        for router in (support_router, subscribe_router, support_start_router):
            sdp.include_router(router)
        bridge.register_support_bot(support_bot)
        await _set_commands(
            support_bot,
            [
                ("start", "فتح خدمة الدعم"),
                ("support", "فتح خدمة الدعم"),
            ],
        )
        await _delete_webhook(support_bot)
        pollers.append(_poll_and_recover(sdp, support_bot))
        print("[bot] dedicated support bot active", flush=True)
    else:
        # لا بوت دعم — رسائل الدعم تمر عبر البوت الرئيسي حصرياً
        bridge.register_support_bot(None)
        print(f"[bot] support inline (mode={config.SUPPORT_MODE})", flush=True)

    try:
        await asyncio.gather(*pollers)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        for b in (bot, support_bot):
            if b is not None:
                try:
                    await b.session.close()
                except Exception:
                    pass