"""Minimal TypeSafe System One client: one fast question per tool call.

The whole API is a single POST — the conversation state plus a mapping of questions — and
the answer comes back per question, with a probability distribution over the choices. The
compaction engine only needs the "drop" probability, which keeps this client tiny.

The key comes from ``TYPESAFE_API_KEY`` (the same key the TypeSafe Jev plugin uses).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

URL = "https://api.typesafe.ai/v1/systemone"


class MissingKey(RuntimeError):
    """No API key in the environment — the caller decides what to do without one."""


def api_key() -> str:
    key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
    if not key:
        raise MissingKey("TYPESAFE_API_KEY is not set")
    return key


def ask(
    state: str, questions: dict, model: str = "jev-latest", timeout: float = 90, tries: int = 3
) -> tuple[dict, float]:
    """Ask one round of questions about ``state``. Returns (response, seconds)."""
    body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
    headers = {"Authorization": "Bearer " + api_key(), "Content-Type": "application/json"}
    started = time.time()
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(URL, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode()), time.time() - started
        except urllib.error.HTTPError as err:
            last = err
            if err.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                # The service says how long to wait; guessing shorter just burns attempts.
                wait = float(err.headers.get("retry-after") or 2**attempt)
                time.sleep(min(wait, 30))
                continue
            raise
        except urllib.error.URLError as err:
            last = err
            if attempt < tries - 1:
                time.sleep(2**attempt)
                continue
            raise
    raise last if last else RuntimeError("TypeSafe call failed")
