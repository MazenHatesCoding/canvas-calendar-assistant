"""Shared by the web functions in api/: checking the caller and sending the reply."""

import hmac
import json


def same_secret(given, expected):
    """True if the caller presented the expected secret. An empty expected secret never
    matches, so a missing setting closes the door instead of opening it."""
    return bool(expected) and hmac.compare_digest((given or "").encode(), expected.encode())


def reply(handler, status, text, content_type="text/plain; charset=utf-8"):
    body = text.encode()
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def reply_json(handler, status, data):
    reply(handler, status, json.dumps(data), "application/json")


def read_json(handler, limit=1_000_000):
    """The request body as a dict, or None if it is not one."""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
        data = json.loads(handler.rfile.read(min(length, limit)))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def bearer(handler):
    """The secret from an "Authorization: Bearer ..." header, or ""."""
    value = handler.headers.get("Authorization") or ""
    return value[7:] if value.startswith("Bearer ") else ""
