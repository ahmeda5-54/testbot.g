import asyncio
import importlib
import time

from aiogram import Bot, Dispatcher, Router
from aiogram.types import BotCommand

import config
import db
from bot import bridge, license as license_mod, texts

# الوحدات تُستورد مرة وتُعاد تحميلها داخل run() لبناء Routers جديدة كل دورة.
# أسماء مستقلة عن متغيّر run() المحلي `bot` (كائن aiogram.Bot) لئلا يُظلل الحزمة.
from bot import handlers_admin as _mod_admin
from bot import handlers_chat_member as _mod_chat_member
from bot import handlers_start as _mod_start
from bot import handlers_support as _mod_support
from bot import handlers_support_pointer as _mod_support_pointer
from bot import handlers_support_start as _mod_support_start
from bot import handlers_subscribe as _mod_subscribe
from bot import handlers_verify as _mod_verify


def _fresh_router(module) -> Router:
    """يعيد تحميل وحدة المعالجات لتنشئ Router جديداً بالكامل.

    aiogram يرفض ربط نفس كائن Router بديسباتشر جديد، وrewire المبني على
    إعادة تشغيل run() يحتاج ديسباتشرات جديدة دائماً، لذا يجب أن يكون كل
    Router طازجاً في كل دورة. reload يعيد تنفيذ الوحدة فيُسجَّل الـ Router
    الجديد بحالاته المناسبة (الوحدات التابعة flow/texts/db لا تُعاد تحميلها)."""
    return importlib.reload(module).router


async def _channel_diagnostic(bot: Bot) -> None:
    """يطبع حالة القناة والإعدادات في بداية التشغيل ليسهل تتبع المشاكل."""
    try:
        me = await bot.get_me()
        print(
            f"[diag] bot=@{me.username} id={me.id} "
            f"channel_id={config.CHANNEL_ID} "
            f"username_env={config.BOT_USERNAME!r}",
            flush=True,
        )
        if config.CHANNEL_ID:
            chat = await bot.get_chat(config.CHANNEL_ID)
            print(
                f"[diag] channel type={chat.type} title={getattr(chat, 'title', '')} "
                f"id={chat.id}",
                flush=True,
            )
            member = await bot.get_chat_member(config.CHANNEL_ID, me.id)
            print(f"[diag] channel role={member.status}", flush=True)
    except Exception as exc:
        print(f"[diag] channel diagnostic failed: {exc!r}", flush=True)


async def _expiry_loop(bot: Bot) -> None:
    """يراقب انتهاء المهل والإزالة التلقائية (المنتهية والمرفوضة) بعد مهلة السماح."""
    failed_notified: set[int] = set()  # من أُبلغ عن فشل إزالته مسبقاً — نكتفي بمرة
    while True:
        try:
            expired = db.expire_overdue()
            for member in expired:
                bridge.notify_admins(texts.admin_expired(member), config.ADMIN_IDS)
            grace = db._int_setting("auto_remove_grace_hours", 0)
            due = db.get_auto_removal_due(
                grace,
                db.get_setting_flag("auto_remove_expired", "0"),
                db.get_setting_flag("auto_remove_rejected", "0"),
            )
            for member in due:
                uid = member["telegramUserId"]
                kind = "expired" if member["status"] == db.EXPIRED else "rejected"
                ok, err = await bridge._remove_member(uid)
                if not ok:
                    # أبقِ الحالة (EXPIRED/REJECTED) ليعاد فحصه في الدورة القادمة،
                    # وأبلغ الأدمن مرة واحدة فقط عند أول فشل.
                    if uid not in failed_notified:
                        failed_notified.add(uid)
                        message = (
                            texts.admin_auto_removed(member, kind, grace)
                            + f"\n\n⚠️ تعذّر الحذف من المجموعة: {err}\n"
                            "سيُعاد الحذف تلقائياً في الدورة القادمة."
                        )
                        bridge.notify_admins(message, config.ADMIN_IDS)
                    continue
                failed_notified.discard(uid)
                db.set_status(uid, db.REMOVED)
                bridge.notify_admins(
                    texts.admin_auto_removed(member, kind, grace),
                    config.ADMIN_IDS,
                )
        except Exception as exc:
            print(f"[expiry] loop error: {exc!r}", flush=True)
        await asyncio.sleep(60)


async def _license_watch() -> None:
    """تنبيهات الترخيص للأدمن (انتهاء/توشك على الانتهاء) عبر تلغرام."""

    async def _notify(st: dict) -> None:
        message = texts.license_admin_warning(st)
        if message:
            try:
                bridge.notify_admins(message, config.ADMIN_IDS)
            except Exception as exc:
                print(f"[license] notify failed: {exc!r}", flush=True)

    while True:
        try:
            if not config.license_enforced():
                await asyncio.sleep(600)
                continue
            st = license_mod.state()
            if not st["valid"] and st["kind"]:
                # انتهت صلاحية النسخة — تحذير واحد
                if db.get_setting("license_warn_expired", "0") != "1":
                    db.set_setting("license_warn_expired", "1")
                    await _notify(st)
            elif st["trial"] and st["remaining_days"] is not None and st["remaining_days"] <= 3:
                # نسخة تجريبية توشك على الانتهاء — تذكير يومي
                today = time.strftime("%Y-%m-%d")
                if db.get_setting("license_warn_day", "") != today:
                    db.set_setting("license_warn_day", today)
                    await _notify(st)
            elif not st["valid"]:
                # غير مفعّلة أصلاً — تحذير واحد
                if db.get_setting("license_warn_none", "0") != "1":
                    db.set_setting("license_warn_none", "1")
                    await _notify(st)
        except Exception as exc:
            print(f"[license] watch error: {exc!r}", flush=True)
        await asyncio.sleep(300)


async def _posting_loop(bot: Bot) -> None:
    """ينشر الرسائل اليومية المجدولة في القناة عند حلول موعدها.

    النشر يعتمد التوقيت المحلي للخادم (نفس توقيت لوحة التحكم)؛ يُستهلك
    الموعد بعد أول محاولة مهما كانت نتيجتها لتفادي إعادة المحاولة اللانهائية،
    ويُبلَّغ الأدمن عن الفشل عبر تلغرام."""
    from bot import channel

    while True:
        try:
            for post in db.get_due_scheduled_posts():
                post_id = post["id"]
                ok, note = await channel.publish_scheduled_post(bot, post)
                db.mark_scheduled_post_sent(
                    post_id, ok, note or ("تم النشر" if ok else "")
                )
                if ok:
                    print(
                        f"[posting] published #{post_id} {post['title']!r} "
                        f"pin={post['pin']} note={note or '-'}",
                        flush=True,
                    )
                else:
                    print(
                        f"[posting] failed #{post_id} {post['title']!r}: {note}",
                        flush=True,
                    )
                    bridge.notify_admins(
                        f"⚠️ فشل نشر الرسالة المجدولة «{post['title']}» في القناة:\n"
                        f"{note}",
                        config.ADMIN_IDS,
                    )
        except Exception as exc:
            print(f"[posting] loop error: {exc!r}", flush=True)
        await asyncio.sleep(45)


async def _reminder_loop(bot: Bot) -> None:
    """تذكير تلقائي للمشتركين قبل انتهاء مهلة إرسال بيانات التحقق.

    يُرسَل التذكير أولاً ثم يُسجَّل عند نجاح الإرسال فقط — فلا يُفقد عضوٌ
    تذكيره بسبب فشل مؤقت، ويظهر الوقت الفعلي المتبقي (لا حدّ التفعيل)."""
    from aiogram.exceptions import (
        TelegramBadRequest,
        TelegramForbiddenError,
        TelegramNetworkError,
    )

    while True:
        try:
            if db.get_setting_flag("reminder_enabled", "1"):
                h1 = db._int_setting("reminder_hours_1", 6)
                h2 = db._int_setting("reminder_hours_2", 1)
                due1, due2 = db.get_reminders_due(h1, h2)
                for member in due1:
                    deadline_seconds = db.deadline_seconds_left(member)
                    if deadline_seconds is None or deadline_seconds <= 0:
                        continue
                    text = texts.deadline_reminder(
                        member, int(deadline_seconds), second=False
                    )
                    try:
                        await bridge._send_with_retry(
                            lambda: bot.send_message(
                                member["telegramUserId"], text
                            )
                        )
                    except (TelegramForbiddenError, TelegramBadRequest) as exc:
                        # لا يمكن الوصول للعضو نهائياً (حظر/إلغاء) — سجّل كمرسل
                        # لنتجنب المحاولة كل دورة، وأبلغ الأدمن عن الخلل.
                        db.mark_reminder_sent(member["telegramUserId"], 1)
                        print(
                            f"[reminder] blocked/unreachable uid="
                            f"{member['telegramUserId']}: {exc!r}",
                            flush=True,
                        )
                        continue
                    except Exception as exc:
                        print(
                            f"[reminder] send failed uid="
                            f"{member['telegramUserId']}: {exc!r}",
                            flush=True,
                        )
                        continue  # لا نُسجّله — يُعاد في الدورة القادمة
                    db.mark_reminder_sent(member["telegramUserId"], 1)
                for member in due2:
                    deadline_seconds = db.deadline_seconds_left(member)
                    if deadline_seconds is None or deadline_seconds <= 0:
                        continue
                    text = texts.deadline_reminder(
                        member, int(deadline_seconds), second=True
                    )
                    try:
                        await bridge._send_with_retry(
                            lambda: bot.send_message(
                                member["telegramUserId"], text
                            )
                        )
                    except (TelegramForbiddenError, TelegramBadRequest) as exc:
                        db.mark_reminder_sent(member["telegramUserId"], 2)
                        print(
                            f"[reminder2] blocked/unreachable uid="
                            f"{member['telegramUserId']}: {exc!r}",
                            flush=True,
                        )
                        continue
                    except Exception as exc:
                        print(
                            f"[reminder2] send failed uid="
                            f"{member['telegramUserId']}: {exc!r}",
                            flush=True,
                        )
                        continue
                    db.mark_reminder_sent(member["telegramUserId"], 2)
        except Exception as exc:
            print(f"[reminder] loop error: {exc!r}", flush=True)
        await asyncio.sleep(30)


async def _clone_loop(bot: Bot) -> None:
    """يفحص القنوات المسجلة للمراقبة دورياً، ويفحص فوراً عند وجود علم
    اللوحة (زر «فحص الآن»). يُرسل تنبيهاً للأدمن عند تنبيهات جديدة."""
    from bot import clone_scan

    last_scan = 0.0
    while True:
        try:
            force = False
            if config.CLONE_SCAN_FLAG.exists():
                try:
                    config.CLONE_SCAN_FLAG.unlink()
                except OSError:
                    pass
                force = True
            hours = db._int_setting("clone_check_hours", 4)
            interval = hours * 3600 if hours > 0 else 0
            due = force or (
                interval > 0
                and (last_scan == 0.0 or time.monotonic() - last_scan >= interval)
            )
            if due:
                result = await clone_scan.scan_clones(bot)
                if result.get("new_alerts"):
                    clone_scan.notify_new_alerts(result["new_alerts"])
                last_scan = time.monotonic()
                print(
                    f"[clone] scan scanned={result.get('scanned')} "
                    f"unreach={result.get('unreachable')} "
                    f"new={len(result.get('new_alerts') or [])} "
                    f"err={result.get('error')}",
                    flush=True,
                )
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


async def _restart_watcher(dispatchers: list[Dispatcher]) -> None:
    """ينتظر علم إعادة التشغيل (من لوحة الإعدادات)، ثم يوقف كل الـ pollers
    بسلاسة واحداً تلو الآخر (الرئيسي + بوت الدعم المخصص إن وُجد).

    *noqa*: watcher واحد شامل إلزامي: سابقاً كان لكل poller watcher مستقل،
    وأول watcher يلتقط العلم ويحذفه فلا يرى الثاني العلم أبداً، فيبقى بوت
    الدعم يpoll إلى الأبد ولا يكتمل asyncio.gather → يتجمد rewiring ويعود
    run() إلى _supervise في إعادة تشغيل لانهائية، ويتوقف البوت الرئيسي عن
    استقبال التحديثات بعد أول حفظ إعدادات عامة.

    stop_polling() يرمي RuntimeError لو لم تكن polling قد بدأت، لذا ننتظر
    حتى يمسك كل dispatcher بأنه يعمل فعلاً (نفس حراسة aiogram الداخلية).
    العلم يُحذف هنا لئلا نتكرر في إعادة تشغيل لا نهائية.
    """
    while not config.RESTART_FLAG.is_file():
        await asyncio.sleep(1)
    try:
        config.RESTART_FLAG.unlink()
    except OSError:
        pass
    print("[bot] restart flag detected — stopping all pollers to rewire", flush=True)
    for dispatcher in dispatchers:
        # ننتظر حتى بدء polling فعلياً لهذا الـ dispatcher قبل إيقافه
        while True:
            running_lock = getattr(dispatcher, "_running_lock", None)
            if running_lock is None or not running_lock.locked():
                await asyncio.sleep(0.2)
                continue
            try:
                await dispatcher.stop_polling()
            except RuntimeError:
                await asyncio.sleep(0.5)
                continue
            break


async def _poll_and_recover(
    dispatcher: Dispatcher, bot: Bot, restart_watcher: asyncio.Task
) -> None:
    """يشغّل polling لهذا البوت ويعيد التشغيل عند السقوط. عند عودة
    start_polling بسلاسة ننتظر اكتمال watcher المشترك (الذي يوقف كل
    البوتات) ثم نعود ليُعيد run() بناء كل شيء بقيم الإعدادات الجديدة.

    الانتظار على watcher ضروري: لو عاد poller الرئيسي قبل أن يكمل watcher
    إيقاف بوت الدعم ثم أعدنا تشغيله فوراً، تعارض إعادة البناء. الانتظار حتى
    يكتمل إيقاف الجميع يضمن رحلة نظيفة واحدة إلى run() التالي."""
    while True:
        try:
            await dispatcher.start_polling(bot, polling_timeout=3)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[bot] polling crashed: {exc!r}, restarting in 5s", flush=True)
            await asyncio.sleep(5)
            continue
        # عاد بسلاسة = أُوقف لسبب ما؛ انتظر إنهاء watcher إن كان يعمل
        if not restart_watcher.done():
            try:
                await asyncio.wait_for(asyncio.shield(restart_watcher), timeout=25)
            except asyncio.TimeoutError:
                pass
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
    config.apply_db_overrides()
    bot = Bot(config.BOT_TOKEN, proxy=config.PROXY_URL)
    dp = Dispatcher()

    # Routers جديدة في كل دورة — ترتيب reload مهم: handlers_subscribe يستورد
    # دوال التحقق من handlers_verify، لذا يُعاد تحميل verify أولاً.
    start_router = _fresh_router(_mod_start)
    verify_router = _fresh_router(_mod_verify)
    admin_router = _fresh_router(_mod_admin)
    support_router = _fresh_router(_mod_support)
    subscribe_router = _fresh_router(_mod_subscribe)
    support_start_router = _fresh_router(_mod_support_start)
    support_pointer_router = _fresh_router(_mod_support_pointer)
    chat_member_router = _fresh_router(_mod_chat_member)

    dp.include_router(start_router)
    # راوتر الأدمن قبل راوتر التحقق إلزامي: معالج «سبب الرفض» في handlers_admin
    # يلتقط النص الحر القادم من الأدمن بعد زر رفض، ولو جاء verify أولاً
    # لابتلع on_any_text النص وردّ بترحيب بدلاً من تطبيق الرفض.
    dp.include_router(admin_router)
    dp.include_router(verify_router)
    # مزامنة شبه فورية للعضوية: البوت الرئيسي أدمن في القناة فيستقبل
    # chat_member لحظياً عند دخول/خروج/حظر أي عضو مسجل.
    dp.include_router(chat_member_router)

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
        asyncio.create_task(_channel_diagnostic(bot)),
        asyncio.create_task(_expiry_loop(bot)),
        asyncio.create_task(_posting_loop(bot)),
        asyncio.create_task(_reminder_loop(bot)),
        asyncio.create_task(_clone_loop(bot)),
        asyncio.create_task(_license_watch()),
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

    support_bot = None
    dispatchers: list[Dispatcher] = [dp]
    if use_dedicated and config.SUPPORT_BOT_TOKEN:
        support_bot = Bot(config.SUPPORT_BOT_TOKEN, proxy=config.PROXY_URL)
        # تحصين: نصحّح اسم بوت الدعم من التوكن الفعلي عند الإقلاع — لو
        # كُتب الاسم خطأً يدوياً (.env/اللوحة) كان زر «تواصل مع الدعم»
        # يوجّه إلى بوت غير موجود ويظهر للمستخدم «المستخدم غير موجود»
        # من تلغرام عند فتح الرابط.
        try:
            support_me = await support_bot.get_me()
            if support_me.username:
                config.SUPPORT_BOT_USERNAME = support_me.username
                print(f"[bot] support username auto-fixed -> @{support_me.username}", flush=True)
        except Exception:
            pass
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
        dispatchers.append(sdp)
        print("[bot] dedicated support bot active", flush=True)
    else:
        # لا بوت دعم — رسائل الدعم تمر عبر البوت الرئيسي حصرياً
        bridge.register_support_bot(None)
        print(f"[bot] support inline (mode={config.SUPPORT_MODE})", flush=True)

    # watcher واحد شامل يوقف كل الـ pollers عند علم إعادة التشغيل —
    # لو كان لكل poller watcher خاص لالتقط الأول العلم وحذفه ولتعطل
    # بقية البوتات في rewiring لا نهائي (انظر الشرح في _restart_watcher).
    restart_watcher = asyncio.create_task(_restart_watcher(dispatchers))
    pollers = [_poll_and_recover(dp, bot, restart_watcher)]
    if support_bot is not None:
        pollers.append(_poll_and_recover(sdp, support_bot, restart_watcher))

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