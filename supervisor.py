import datetime
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
OUT_LOG = DATA_DIR / "supervisor.out.log"
ERR_LOG = DATA_DIR / "supervisor.err.log"


def _stamp(text: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(OUT_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"[supervisor {ts}] {text}\n")


def main() -> None:
    _stamp("starting...")
    while True:
        _stamp("launching bot (run.py)")
        with open(OUT_LOG, "ab") as out, open(ERR_LOG, "ab") as err:
            process = subprocess.Popen(
                [sys.executable, "run.py"],
                cwd=str(BASE_DIR),
                stdout=out,
                stderr=err,
            )
        code = process.wait()
        _stamp(f"bot exited with code {code}, restarting in 3s")
        time.sleep(3)


if __name__ == "__main__":
    main()