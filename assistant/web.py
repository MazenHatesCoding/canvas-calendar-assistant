"""Shared by the two web functions in api/: checking the caller and sending the reply."""

import hmac

from .config import setting


def has_secret(given, name, prefix=""):
    """True if `given` matches the secret stored in the setting `name`.
    An unset secret never matches, so a missing setting closes the door instead of opening it."""
    expected = setting(name)
    return bool(expected) and hmac.compare_digest((given or "").encode(), (prefix + expected).encode())


def reply(handler, status, text):
    body = text.encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
