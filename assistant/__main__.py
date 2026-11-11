"""Run the hourly check by hand, on your own computer:  python -m assistant

When hosted, the same check is started every hour by api/check.py, and Telegram messages
are answered by api/telegram.py. Add --verbose to print titles and the AI's reasons.
"""

import sys

from . import config
from .core import Assistant, missing_settings


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles choke on Arabic otherwise
    config.load()
    missing = missing_settings()
    if missing:
        sys.exit("Missing settings: " + ", ".join(missing))
    if Assistant("--verbose" in sys.argv).check_canvas() == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
