import os
import shutil
import time

BASE_DIR = "/tmp/agent-runs"

MAX_AGE_SECONDS = 60 * 60 * 2   # 2 horas
MAX_RUNS = 30


def get_runs():
    if not os.path.exists(BASE_DIR):
        return []

    runs = []

    for name in os.listdir(BASE_DIR):
        path = os.path.join(BASE_DIR, name)

        if not os.path.isdir(path):
            continue

        runs.append((path, os.path.getmtime(path)))

    return runs


def cleanup():
    runs = get_runs()

    if not runs:
        return

    now = time.time()

    # ordena por más recientes primero
    runs.sort(key=lambda x: x[1], reverse=True)

    for i, (path, mtime) in enumerate(runs):

        age = now - mtime

        # 1. conserva los últimos N runs
        if i < MAX_RUNS:
            continue

        # 2. borra por TTL
        if age > MAX_AGE_SECONDS:
            print(f"[cleanup] removing {path}")
            shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    cleanup()