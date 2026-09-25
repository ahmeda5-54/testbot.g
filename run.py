import asyncio
import threading
import time

import config


def run_dashboard() -> None:
    from dashboard.app import app

    app.run(
        host=config.DASHBOARD_HOST,
        port=config.DASHBOARD_PORT,
        threaded=True,
        use_reloader=False,
    )


async def _supervise() -> None:
    from bot.main import run

    # لا نسمح بأي استثناء بإسقاط العملية إطلاقاً — لو سقط البوت
    # نعيد تشغيله بعد 5 ثوانٍ واللوحة تبقى حية طوال الوقت.
    while True:
        try:
            await run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[main] bot crashed: {exc!r}; restarting in 5s", flush=True)
            await asyncio.sleep(5)


def main() -> None:
    import db

    db.init_db()
    # مهم قبل بدء اللوحة: يطبق إعدادات معالج الإعداد الأول المخزنة في DB
    # (البائع/المشتري يضبط كل شيء من /setup دون لمس ملف .env)
    config.apply_db_overrides()

    threading.Thread(target=run_dashboard, daemon=True).start()
    print(
        f"Dashboard: http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}/",
        flush=True,
    )

    # حلقة asyncio واحدة دائمة: هذا يبقي bridge._loop صالحاً دائماً فلا
    # تنكسر استدعاءات اللوحة (run_coroutine_threadsafe) بعد إعادة البناء،
    # بينما run() يُعاد نداؤها داخلها لبناء بوتات/Routers جديدة بقيم محدّثة.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_supervise())
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        finally:
            loop.close()


if __name__ == "__main__":
    main()