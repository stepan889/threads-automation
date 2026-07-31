"""Run database migrations, API and Telegram worker in one Amvera container."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def _run_once(command: list[str]) -> None:
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> None:
    _run_once(["alembic", "upgrade", "head"])

    if os.getenv("THREADS_BOOTSTRAP_TOKEN"):
        _run_once([sys.executable, "-m", "app.bootstrap_threads"])

    processes = [
        subprocess.Popen(
            [
                "uvicorn",
                "app.api:app",
                "--host",
                "0.0.0.0",
                "--port",
                os.getenv("PORT", "8000"),
            ]
        ),
        subprocess.Popen([sys.executable, "-m", "app.bot.main"]),
    ]

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    exit_code = 0
    try:
        while True:
            for process in processes:
                result = process.poll()
                if result is not None:
                    exit_code = result or 1
                    stop(signal.SIGTERM, None)
                    raise SystemExit(exit_code)
            time.sleep(1)
    finally:
        stop(signal.SIGTERM, None)
        deadline = time.monotonic() + 10
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
        for process in processes:
            process.wait()


if __name__ == "__main__":
    main()
