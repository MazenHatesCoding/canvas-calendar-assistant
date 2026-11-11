"""One-time step: tell Telegram where the hosted assistant lives, so replies are instant.

    python -m assistant.connect_telegram https://your-project.vercel.app

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET (the same value the hosted assistant
has) in .env. Run it again whenever the address or the secret changes.
"""

import sys

from . import config, telegram as tg
from .config import setting


def main():
    config.load()
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        sys.exit("Usage: python -m assistant.connect_telegram https://your-project.vercel.app")
    token, secret = setting("TELEGRAM_BOT_TOKEN"), setting("TELEGRAM_WEBHOOK_SECRET")
    if not token or not secret:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET in .env first.")
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
