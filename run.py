import asyncio
import threading

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
        f"Dashboard: http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}/"
    )

    from bot.main import run

    asyncio.run(run())


if __name__ == "__main__":
    main()
