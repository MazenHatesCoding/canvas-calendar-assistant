"""One-time setup: sign in to Google and create the assistant's calendar.

Run:  python -m assistant.connect_google

Opens a browser window. Afterwards token.json holds the sign-in, and the new calendar's ID
is saved in .env as GOOGLE_CALENDAR_ID.
"""

from dotenv import set_key
from google_auth_oauthlib.flow import InstalledAppFlow

from . import config
from .config import CREDENTIALS_FILE, ENV_FILE, TOKEN_FILE, setting
from .gcal import SCOPES, create_calendar

CALENDAR_NAME = "University"


def main():
    config.load()
    if not CREDENTIALS_FILE.exists():
        raise SystemExit("credentials.json is missing from the project folder.")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    credentials = flow.run_local_server(port=0, timeout_seconds=540)
    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
    print("Signed in. Saved token.json.")

    if setting("GOOGLE_CALENDAR_ID"):
        print("GOOGLE_CALENDAR_ID is already set, keeping the existing calendar.")
        return
    calendar_id = create_calendar(credentials, CALENDAR_NAME, setting("TIMEZONE", "Africa/Cairo"))
    set_key(str(ENV_FILE), "GOOGLE_CALENDAR_ID", calendar_id, quote_mode="never")
    print(f'Created the "{CALENDAR_NAME}" calendar and saved its ID in .env.')


if __name__ == "__main__":
    main()
