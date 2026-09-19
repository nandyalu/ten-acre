"""Posting to Discord, through a webhook.

**The app only ever posts.** It reads nothing and responds to nothing, and it
has had no commands since 2026-09-01. A webhook is therefore the whole
requirement: a URL copied from a channel's settings, and one HTTP POST.

What that replaced was a bot — an application, a token, OAuth scopes, an invite
URL, a gateway connection held open for the life of the process, and the
`discord.py` dependency threaded through startup and shutdown. All of it to
send a message.

**Set `DISCORD_WEBHOOK_URL` or leave it unset.** Unset disables notifications
and changes nothing else; the site and the agent behave identically.

A failed post is logged and swallowed. Discord being unreachable must never
stop the agent trading or the scheduler running — the notification is a copy of
what the database already holds, not the record itself.
"""
import asyncio
import json
import logging
import os
import urllib.error
import urllib.request

from backend.notifications.embed import Embed

log = logging.getLogger("ten-acre.notify")

# Discord rejects a post whose content exceeds this.
CONTENT_MAX = 2000
_TIMEOUT_SECONDS = 15


def webhook_url() -> str | None:
    return (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip() or None


def is_configured() -> bool:
    return webhook_url() is not None


def _post(url: str, payload: dict) -> None:
    """Blocking. Called through a thread so the event loop keeps running."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "ten-acre"},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS):
        pass


async def notify(message: str | None = None, embed: Embed | None = None) -> bool:
    """Post to the configured channel. True when it went, False otherwise.

    Never raises. Every caller is a scheduled job or a broker callback, and
    none of them should fail because a chat service did.
    """
    url = webhook_url()
    if url is None:
        return False
    if message is None and embed is None:
        return False

    payload: dict = {}
    if message:
        payload["content"] = message[:CONTENT_MAX]
    if embed is not None:
        payload["embeds"] = [embed.to_dict()]

    try:
        await asyncio.to_thread(_post, url, payload)
        return True
    except urllib.error.HTTPError as exc:
        # 429 included. Retrying here would queue behind a rate limit the app
        # cannot see the window for, and the message is not worth blocking a
        # job over.
        log.warning("Discord refused the post: %s %s", exc.code, exc.reason)
    except Exception:
        log.warning("Could not reach Discord", exc_info=True)
    return False
