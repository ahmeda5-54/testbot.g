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


def main() -> None:
    import db

    db.init_db()

    threading.Thread(target=run_dashboard, daemon=True).start()
    print(
        f"Dashboard: http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}/",
        flush=True,
    )

    from bot.main import run

    # لا نسمح بأي استثناء بإسقاط العملية إطلاقاً — لو سقط البوت
    # نعيد تشغيله بعد 5 ثوانٍ واللوحة تبقى حية طوال الوقت.
    while True:
        try:
            asyncio.run(run())
        except Exception as exc:
            print(f"[main] bot crashed: {exc!r}; restarting in 5s", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
