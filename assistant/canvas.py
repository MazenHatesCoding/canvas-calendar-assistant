"""Read-only access to Canvas: courses, announcements and inbox messages."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import requests


class CanvasProblem(Exception):
    """Canvas could not be read. The message is safe to show to the student."""


@dataclass
class Item:
    """One thing a professor or TA posted, in a shape the rest of the app shares."""

    id: str  # unique and stable, e.g. "announcement-123" - used to avoid duplicates
    kind: str  # "announcement" or "message"
    course: str
    title: str
    body: str  # plain text
    posted_at: datetime  # timezone-aware
    url: str


class _TextExtractor(HTMLParser):
    BLOCK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"}

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html):
    parser = _TextExtractor()
    parser.feed(html or "")
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def parse_time(text):
    """A Canvas timestamp as an aware datetime, or None if it is missing or malformed."""
    try:
        moment = datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class CanvasClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _request(self, url, params):
        # Reading changes nothing, so a request that got no answer is tried once more
        # before the student is told that Canvas cannot be reached.
        for attempt in (1, 2):
            try:
                response = self.session.get(url, params=params, timeout=20)
                break
            except requests.RequestException:
                if attempt == 2:
                    raise CanvasProblem("Could not reach Canvas.") from None
        if response.status_code == 401:
            raise CanvasProblem(
                "Canvas rejected the access token. It has probably expired: create a new one "
                "and save it as CANVAS_TOKEN."
            )
        if not response.ok:
            raise CanvasProblem(f"Canvas returned an error ({response.status_code}).")
        return response

    def _get(self, path, params=None):
        return self._request(f"{self.base_url}/api/v1{path}", params).json()

    def _get_all(self, path, params=None):
        """GET a list endpoint, following Canvas's pagination links."""
        url = f"{self.base_url}/api/v1{path}"
        results = []
        while url:
            response = self._request(url, params)
            results.extend(response.json())
            url = response.links.get("next", {}).get("url")
            params = None  # the "next" link already carries the query string
        return results

    def active_courses(self):
        courses = self._get_all("/courses", {"enrollment_state": "active", "per_page": 100})
        # Courses the student can no longer access come back without a name.
        return [c for c in courses if c.get("name")]

    def announcements(self, courses, since):
        if not courses:
            return []
        names = {f"course_{c['id']}": c["name"] for c in courses}
        raw = self._get_all(
            "/announcements",
            {
                "context_codes[]": list(names),
                "start_date": since.isoformat(),
                "end_date": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "per_page": 50,
            },
        )
        return [
            Item(
                id=f"announcement-{a['id']}",
                kind="announcement",
                course=names.get(a.get("context_code"), "Unknown course"),
                title=a.get("title") or "",
                body=html_to_text(a.get("message")),
                posted_at=parse_time(a.get("posted_at")) or datetime.now(timezone.utc),
                url=a.get("html_url") or "",
            )
            for a in raw
        ]

    def messages(self, since):
        """Inbox messages written by other people. Each message in a thread is its own Item."""
        my_id = self._get("/users/self").get("id")
        items = []
        for conv in self._get_all("/conversations", {"per_page": 50}):
            last = parse_time(conv.get("last_message_at"))
            if not last or last < since:
                continue
            # auto_mark_as_read=false so the script never hides unread messages from the student.
            full = self._get(f"/conversations/{conv['id']}", {"auto_mark_as_read": "false"})
            for msg in full.get("messages", []):
                created = parse_time(msg.get("created_at"))
                if not created or created < since or msg.get("author_id") == my_id:
                    continue
                items.append(
                    Item(
                        id=f"message-{msg['id']}",
                        kind="message",
                        course=conv.get("context_name") or "Inbox",
                        title=conv.get("subject") or "(no subject)",
                        body=html_to_text(msg.get("body")),
                        posted_at=created,
                        url=f"{self.base_url}/conversations/{conv['id']}",
                    )
                )
        return items

    def recent_items(self, lookback_days):
        """Everything posted in the last `lookback_days`, oldest first."""
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        items = self.announcements(self.active_courses(), since) + self.messages(since)
        return sorted(items, key=lambda item: item.posted_at)
