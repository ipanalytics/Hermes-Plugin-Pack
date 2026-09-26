"""skill-factory — watch a session and turn its repeated workflow into a skill.

The upstream project (github.com/Romanescu11/hermes-skill-factory) targets an older plugin
API (``@ctx.command`` / ``@ctx.on``). This is the same idea re-implemented on the current
Hermes plugin contract (``register(ctx)`` + ``ctx.register_hook`` / ``ctx.register_command``),
so ``hermes plugins doctor`` passes and the surface actually binds.

What it does:
  * passively records tool calls through the ``post_tool_call`` hook (bounded state file),
  * surfaces repeated workflows as *candidates*,
  * ``/skill-factory-propose`` hands the agent a concrete work order: read the recorded
    events, decide the reusable procedure, and write it with the skill tooling.

The rule behind it: a skill written from a guess is noise, a skill written from a
repetition is one you keep. So the plugin never invents a procedure — it only points at
what actually happened twice.

State: $HERMES_HOME/plugin-data/skill-factory/events.json (last 500 events) and
proposals.json (candidates the user accepted).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections import Counter
from itertools import pairwise
from pathlib import Path

MAX_EVENTS = 500


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _state_dir() -> Path:
    path = _home() / "plugin-data" / "skill-factory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load(name: str, default):
    path = _state_dir() / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(name: str, payload) -> None:
    path = _state_dir() / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic: a half-written window is worse than none


def _record_event(tool_name: str, status: str | None, session_id: str | None) -> None:
    events = _load("events.json", [])
    events.append(
        {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": tool_name,
            "status": status or "ok",
            "session": session_id or "",
        }
    )
    del events[:-MAX_EVENTS]
    _save("events.json", events)


def _candidates(limit: int = 5) -> list[dict]:
    """Repeated tools and tool pairs in the recorded window = workflows worth a skill."""
    events = _load("events.json", [])
    tools = [e.get("tool", "") for e in events if e.get("tool")]
    singles = Counter(tools)
    pairs = Counter(pairwise(tools))
    ordered = [{"pattern": t, "count": c} for t, c in singles.most_common(limit)]
    ordered += [{"pattern": f"{a} -> {b}", "count": c} for (a, b), c in pairs.most_common(limit)]
    return [item for item in ordered if item["count"] > 1]


def _cmd_propose(raw_args: str = "") -> str:
    events = _load("events.json", [])
    candidates = _candidates()
    if not events:
        return (
            "skill-factory: no tracked tool calls yet. Run some work first — the hook "
            "records every tool call of this session once the plugin is enabled."
        )
    lines = [
        "skill-factory — propose a skill from this session.",
        f"Tracked events: {len(events)} (window of {MAX_EVENTS}).",
        "",
        "Repeated patterns (candidate workflows):",
    ]
    for item in candidates[:8]:
        lines.append(f"  - {item['pattern']} ×{item['count']}")
    if not candidates:
        lines.append("  - none repeated yet; a one-off is not worth a skill")
    lines += [
        "",
        "Work order for the agent (do this instead of guessing):",
        "  1. Read the recorded events for this session and pick ONE reusable procedure.",
        "  2. Write it with skill_manage(action='create', ...) — trigger first in the",
        "     description, imperative rules with the reason behind each, no narration.",
        "  3. Record the accepted candidate name with /skill-factory-save <name>.",
        f"  (extra args ignored: {raw_args!r})" if raw_args.strip() else "",
    ]
    return "\n".join(line for line in lines if line is not None)


def _cmd_status(raw_args: str = "") -> str:
    events = _load("events.json", [])
    proposals = _load("proposals.json", [])
    tools = Counter(e.get("tool", "") for e in events if e.get("tool"))
    errors = sum(1 for e in events if e.get("status") not in ("ok", None, ""))
    top = ", ".join(f"{t}×{c}" for t, c in tools.most_common(5)) or "—"
    return (
        "skill-factory status\n"
        f"  tracked events : {len(events)} (errors: {errors})\n"
        f"  top tools      : {top}\n"
        f"  saved proposals: {len(proposals)}"
        + (f" ({', '.join(p.get('name', '?') for p in proposals[-5:])})" if proposals else "")
    )


def _cmd_list(raw_args: str = "") -> str:
    proposals = _load("proposals.json", [])
    if not proposals:
        return "skill-factory: no saved proposals yet."
    return "skill-factory proposals:\n" + "\n".join(
        f"  - {p.get('name', '?')} ({p.get('ts', '')})" for p in proposals
    )


def _cmd_save(raw_args: str = "") -> str:
    name = (raw_args or "").strip().split()[0] if (raw_args or "").strip() else ""
    if not name:
        return "usage: /skill-factory-save <skill-name>"
    proposals = _load("proposals.json", [])
    proposals.append({"name": name, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    _save("proposals.json", proposals)
    return f"skill-factory: recorded proposal '{name}' ({len(proposals)} total)."


def _cmd_clear(raw_args: str = "") -> str:
    _save("events.json", [])
    return "skill-factory: event window cleared."


def _on_post_tool_call(**kwargs) -> None:
    # An observer must never be able to break the tool path it is watching.
    with contextlib.suppress(Exception):
        _record_event(
            str(kwargs.get("tool_name") or ""), kwargs.get("status"), kwargs.get("session_id")
        )  # a recorder that throws is worse than no recorder


def register(ctx) -> None:
    """Bind hooks and slash commands on the current Hermes plugin API."""
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_command(
        "skill-factory-propose",
        _cmd_propose,
        description="Propose the most reusable workflow of this session as a skill",
    )
    ctx.register_command(
        "skill-factory-status",
        _cmd_status,
        description="Show what skill-factory has tracked this session",
    )
    ctx.register_command(
        "skill-factory-list", _cmd_list, description="List skill proposals recorded so far"
    )
    ctx.register_command(
        "skill-factory-save",
        _cmd_save,
        description="Record an accepted proposal",
        args_hint="<skill-name>",
    )
    ctx.register_command(
        "skill-factory-clear", _cmd_clear, description="Clear the tracked event window"
    )
