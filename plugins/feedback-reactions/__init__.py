"""Reactions become data: a 👍 or 👎 in chat turns into one row of feedback.jsonl.

A reaction is the cheapest signal a person gives: one tap, no words, no meeting.
The host normalises ``message_reaction`` into a ``reaction`` platform event and only
forwards it for authorised people, so nothing needs filtering here. Rows land in the
same file as text verdicts, which means an existing weekly review keeps working
without being changed.

A better observer never breaks the thing it observes: every failure is swallowed.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

POSITIVE = {"👍", "❤", "🔥", "🎉", "🙏", "💯", "✅", "😍", "🤝", "💪", "🥰"}
NEGATIVE = {"👎", "🤮", "🤬", "💩", "😡", "❌", "😤"}


def journal_path() -> pathlib.Path:
    """Feedback journal, next to the other data of this Hermes home."""
    home = pathlib.Path(os.environ.get("HERMES_HOME") or (pathlib.Path.home() / ".hermes"))
    return home / "data" / "feedback.jsonl"


def _verdict(emojis: list[str]) -> int | None:
    """1 = positive, -1 = negative, 0 = neutral reaction, None = not a reaction at all."""
    if any(e in POSITIVE for e in emojis):
        return 1
    if any(e in NEGATIVE for e in emojis):
        return -1
    return 0 if emojis else None


def _on_platform_event(event=None, **_kw):
    """Platform event -> one journal line. Errors are dropped on purpose."""
    try:
        if not isinstance(event, dict) or event.get("event_type") != "reaction":
            return
        payload = event.get("payload") or {}
        emojis = [str(e) for e in (payload.get("emojis") or [])]
        verdict = _verdict(emojis)
        if verdict is None:
            return
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "reaction",
            "platform": event.get("platform") or "unknown",
            "user_id": None,
            "chat_id": payload.get("chat_id"),
            "message_id": payload.get("message_id"),
            "reactions": emojis,
            "verdict": verdict,
            # "counts": the same flag text verdicts carry, so one report reads both kinds.
            "counts": True,
            "source": "chat reaction",
        }
        path = journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        return


def register(ctx) -> None:
    """Hermes plugin contract: register(ctx) plus one hook."""
    ctx.register_hook("gateway_platform_event", _on_platform_event)
