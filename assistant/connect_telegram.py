"""One-time step: tell Telegram where the hosted assistant lives, so replies are instant.

    python -m assistant.connect_telegram https://your-project.vercel.app

Needs TELEGRAM_BOT_TOKEN in .env. The setup page of a hosted copy does the same thing, so
this is only for doing it from your own computer.
"""

import sys

from . import config, telegram as tg
from .config import setting


def main():
    config.load()
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        sys.exit("Usage: python -m assistant.connect_telegram https://your-project.vercel.app")
    token, secret = setting("TELEGRAM_BOT_TOKEN"), config.webhook_secret()
    if not token:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env first.")
    bot = tg.Telegram(token)
    try:
        bot.set_webhook(sys.argv[1].rstrip("/") + "/api/telegram", secret)
        bot.set_commands()  # fills the bot's "/" menu
        info = bot.webhook_info()
    except RuntimeError as error:
        sys.exit(str(error))
    print(f"Telegram now sends messages to {info.get('url')}")
    if info.get("last_error_message"):
        print(f"Telegram's last delivery problem: {info['last_error_message']}")


if __name__ == "__main__":
    main()
