"""Apply Esgram and run the requested Gradle task with the bot's Telegram app ID."""
import os
from pathlib import Path
import re
import subprocess
import sys

from dotenv import dotenv_values


def main():
    if len(sys.argv) < 3:
        raise SystemExit("Usage: build_esgram.py TELEGRAM_CHECKOUT GRADLE_TASK [ARGS...]")
    root = Path(sys.argv[1]).resolve()
    here = Path(__file__).resolve().parent
    for config in (os.environ, dotenv_values(here / ".env", interpolate=False),
                   dotenv_values(here.parent / ".env", interpolate=False)):
        app_id = (config.get("API_ID") or "").strip()
        app_hash = (config.get("API_HASH") or "").strip()
        if not (app_id or app_hash):
            continue
        if (not re.fullmatch(r"[1-9][0-9]{0,9}", app_id) or int(app_id) > 2147483647
                or not re.fullmatch(r"[a-fA-F0-9]{32}", app_hash)):
            raise SystemExit("Invalid or incomplete API_ID/API_HASH pair.")
        break
    else:
        raise SystemExit("Set API_ID and API_HASH or supply android/.env.")
    subprocess.run([sys.executable, str(here / "apply_esgram.py"), str(root)], check=True)
    wrapper = root / ("gradlew.bat" if os.name == "nt" else "gradlew")
    subprocess.run([str(wrapper), *sys.argv[2:]], cwd=root, check=True,
                   env=dict(os.environ, API_ID=app_id, API_HASH=app_hash))


if __name__ == "__main__":
    main()
