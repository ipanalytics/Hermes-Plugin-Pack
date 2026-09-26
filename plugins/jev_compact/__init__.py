"""Context compaction that never summarises — it cuts, and everything left stays verbatim.

The idea (credited to ``tamaratran/fast-jev-compaction``, MIT) is that a summary is a quiet
lie: the model rewrites your history and details drift. So the transcript is not rewritten.
Instead one fast decision call receives the whole conversation — tool results replaced by
short notes, inputs and texts intact — and answers one question per tool call: is this
result still needed? What it calls stale is removed; everything that stays stays exactly as
it was: paths, commands, numbers and errors are never rephrased.

Safeguards, because a context engine sits in the live turn:
  * the first ``protect_first_n`` and the last ``protect_last_n`` messages are never touched;
  * user and assistant text is not rewritten (except a last-resort stage, which marks the
    middle of a long stale message as cut rather than inventing new prose);
  * if the decision model is unreachable, the engine degrades to a deterministic cut
    instead of failing the turn;
  * every decision is logged to ``$HERMES_HOME/data/jev_compact_decisions.jsonl``, so you
    can audit what was thrown away.

Requirements: ``TYPESAFE_API_KEY`` in the environment, a Hermes host with context engines,
and nothing else.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

try:  # inside a Hermes host
    from agent.context_engine import ContextEngine
    from agent.model_metadata import estimate_messages_tokens_rough
except ImportError:  # standalone import (unit tests, docs)

    class ContextEngine:  # type: ignore[no-redef]
        """Minimal stand-in: two protection windows and a name."""

        protect_first_n = 2
        protect_last_n = 6

    def estimate_messages_tokens_rough(messages: list[dict[str, Any]]) -> int:  # type: ignore[misc]
        """~4 characters per token, including tool call arguments."""
        total = 0
        for msg in messages:
            total += len(str(msg.get("content") or ""))
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") if isinstance(call, dict) else None
                total += len(str((fn or {}).get("arguments") or call.get("arguments") or ""))
        return total // 4


try:
    from . import typesafe
except ImportError:  # loaded as a plain module
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import typesafe  # type: ignore[no-redef]

log = logging.getLogger("agent.context_engine.jev")

DROP_P = 0.75  # above this probability a call the model called stale is cut
MAX_ITEMS = 30  # how many calls go into one decision request
STATE_CHARS = 120_000  # how much of the conversation the decision call may see
FAST_TIMEOUT = 25  # it runs inside a live turn, so it must answer quickly
NOTE = "…[removed by context engine, {n} chars]…"


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def decisions_path() -> Path:
    return hermes_home() / "data" / "jev_compact_decisions.jsonl"


def _cfg_int(key: str, default: int) -> int:
    """Threshold from the host config, then the environment, then the default."""
    env = os.environ.get(f"COMPRESSION_{key.upper()}")
    if env and env.strip().isdigit():
        return int(env)
    try:
        import yaml  # optional: only needed to read the host config

        with (hermes_home() / "config.yaml").open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        val = (cfg.get("compression") or {}).get(key)
        return int(val) if val is not None else default
    except Exception:
        return default


def _text_of(msg: dict[str, Any], limit: int) -> str:
    content = msg.get("content")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "tool_result":
                    parts.append(str(block.get("content") or "")[:400])
            else:
                parts.append(str(block))
        content = " ".join(parts)
    text = str(content or "")
    if limit and len(text) > limit:
        return text[:limit] + f" …(+{len(text) - limit} chars)"
    return text


def _calls(msg: dict[str, Any]) -> list[dict[str, Any]]:
    calls = msg.get("tool_calls")
    return [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []


def _call_name_args(call: dict[str, Any], arg_limit: int) -> tuple[str, str]:
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(call.get("name") or fn.get("name") or "tool")
    args = call.get("arguments") if call.get("arguments") is not None else fn.get("arguments")
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False) if args is not None else ""
    if arg_limit and len(args) > arg_limit:
        args = args[:arg_limit] + "…"
    return name, args


class JevCompactionEngine(ContextEngine):
    """Context without paraphrase: stale tool calls removed by a decision model."""

    def __init__(self) -> None:
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.threshold_tokens = _cfg_int("threshold_tokens", 250_000)
        self.context_length = 0
        self.compression_count = 0
        self.last_run: dict[str, Any] = {}
        if not hasattr(self, "protect_first_n"):
            self.protect_first_n = 2
        if not hasattr(self, "protect_last_n"):
            self.protect_last_n = 6
        log.info(
            "context engine ready: threshold %d tokens, decisions %s",
            self.threshold_tokens,
            "on" if self._key_ok() else "off (no TYPESAFE_API_KEY)",
        )

    def _key_ok(self) -> bool:
        try:
            typesafe.api_key()
            return True
        except Exception:
            return False

    @property
    def name(self) -> str:
        return "jev_compact"

    def update_from_response(self, usage: dict[str, Any]) -> None:
        usage = usage or {}
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        self.last_completion_tokens = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        self.last_total_tokens = int(
            usage.get("total_tokens") or (self.last_prompt_tokens + self.last_completion_tokens)
        )
        if not self.context_length:
            self.context_length = int(usage.get("context_length") or 0)

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        tokens = int(prompt_tokens or self.last_prompt_tokens or 0)
        return bool(self.threshold_tokens) and tokens >= self.threshold_tokens

    # ---- what the decision model gets to see ------------------------------------------

    def _state(self, messages: list[dict[str, Any]], arg_limit: int, text_limit: int) -> str:
        out: list[str] = []
        for i, msg in enumerate(messages):
            role = str(msg.get("role") or "?")
            if role == "tool":
                body = _text_of(msg, 0)
                out.append(
                    f"[{i}] tool({msg.get('tool_name') or msg.get('name') or '?'}) "
                    f"→ ok, {len(body)} chars (hidden)"
                )
                continue
            text = _text_of(msg, text_limit)
            line = f"[{i}] {role}: {text}".rstrip()
            for call in _calls(msg):
                name, args = _call_name_args(call, arg_limit)
                line += f"\n      call: {name}({args})"
            out.append(line)
        return "\n".join(out)

    def _fit_state(self, messages: list[dict[str, Any]]) -> tuple[str, dict[str, int]]:
        """Shrink the view until it fits, rather than sending a truncated state."""
        for arg_limit, text_limit in ((1000, 4000), (200, 2000), (60, 800), (0, 400)):
            state = self._state(messages, arg_limit, text_limit)
            if len(state) <= STATE_CHARS:
                return state, {"arg_limit": arg_limit, "text_limit": text_limit}
        return state, {"arg_limit": 0, "text_limit": 400}

    # ---- call / result pairs ----------------------------------------------------------

    def _pairs(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Every tool call with the index of its result, if the result is still around."""
        results = {
            str(m.get("tool_call_id")): i
            for i, m in enumerate(messages)
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        pairs: list[dict[str, Any]] = []
        for i, msg in enumerate(messages):
            if msg.get("role") != "assistant":
                continue
            for n, call in enumerate(_calls(msg)):
                cid = str(call.get("id") or call.get("call_id") or "")
                name, args = _call_name_args(call, 200)
                pairs.append(
                    {
                        "msg": i,
                        "id": cid,
                        "key": f"{i}:{n}",
                        "name": name,
                        "args": args,
                        "result": results.get(cid),
                        "text_kept": bool(str(msg.get("content") or "").strip()),
                    }
                )
        return pairs

    def _ask(self, pairs: list[dict[str, Any]], state: str) -> dict[str, float]:
        """One fast request: which of these calls is nobody going to need again?

        Returns {pair key: probability that the result is no longer needed}. A pair with no
        answer at all is treated as "still needed" — silence must never delete anything.
        """
        verdicts: dict[str, float] = {}
        for start in range(0, len(pairs), MAX_ITEMS):
            chunk = pairs[start : start + MAX_ITEMS]
            questions = {}
            keyed = {}
            for n, pair in enumerate(chunk):
                key = f"t{start + n}"
                keyed[key] = pair
                questions[key] = {
                    "type": "choice",
                    "instructions": (
                        f"Context above. Tool call {pair['name']}({pair['args'][:120]}), "
                        f"its result is in the transcript. Is that result still needed "
                        f"later in this conversation?"
                    ),
                    "criteria": {
                        "keep": "later turns still build on it: the file is being edited, the error "
                        "is being fixed, the data is still in play",
                        "drop": "the conversation has moved on: the result was reference material, "
                        "it is outdated, or nothing refers to it any more",
                    },
                }
            resp, _dt = typesafe.ask(state, questions, timeout=FAST_TIMEOUT, tries=1)
            answers = resp.get("answers") or {}
            for key, pair in keyed.items():
                ans = answers.get(key) or {}
                probs = ans.get("probabilities") or {}
                p_drop = float(probs.get("drop") or 0.0)
                if ans.get("choice") == "drop" and not probs:
                    p_drop = 1.0
                if ans.get("choice") is None and not probs:
                    continue  # no answer for this pair: keep it
                verdicts[pair["key"]] = p_drop
                path = decisions_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "tool": pair["name"],
                                "drop_p": round(p_drop, 3),
                                "kept": p_drop < DROP_P,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        return verdicts

    # ---- the three stages of shrinking -------------------------------------------------

    def _drop(
        self,
        messages: list[dict[str, Any]],
        pairs: list[dict[str, Any]],
        verdicts: dict[str, float],
        head_end: int,
        tail_start: int,
    ) -> tuple[int, int]:
        """Remove what the decision model called stale. Returns (results removed, pairs removed).

        A whole pair (request and result) can only go when both halves sit in the middle and
        the assistant message carries no text of its own — otherwise the result stays, shrunk
        to a note. Losing an assistant's sentence would be rewriting the conversation.
        """
        n_results = n_pairs = 0
        for pair in pairs:
            if verdicts.get(pair["key"], 0.0) < DROP_P:
                continue
            res, call_msg = pair["result"], pair["msg"]
            call_in_middle = head_end <= call_msg < tail_start
            res_in_middle = res is not None and head_end <= res < tail_start
            if res is None:
                if call_in_middle and not pair["text_kept"]:
                    kept = [
                        c
                        for c in _calls(messages[call_msg])
                        if str(c.get("id") or c.get("call_id")) != pair["id"]
                    ]
                    messages[call_msg]["tool_calls"] = kept or None
                    if not kept:
                        n_pairs += 1
                continue
            if not res_in_middle:
                continue
            if call_in_middle and not pair["text_kept"]:
                kept = [
                    c
                    for c in _calls(messages[call_msg])
                    if str(c.get("id") or c.get("call_id")) != pair["id"]
                ]
                messages[call_msg]["tool_calls"] = kept or None
                if not kept and len(_calls(messages[call_msg])) == 0:
                    # the request is gone entirely, so the orphaned result goes with it
                    messages[res]["content"] = ""
                    messages[res]["_jev_removed"] = True
                    n_pairs += 1
                    continue
            body = _text_of(messages[res], 0)
            messages[res]["content"] = NOTE.format(n=len(body))
            n_results += 1
        return n_results, n_pairs

    def _head_end(self, msgs: list[dict[str, Any]]) -> int:
        """The head is untouchable: system messages plus the first user message."""
        first_user = next((i for i, m in enumerate(msgs) if m.get("role") == "user"), -1)
        return max(self.protect_first_n, first_user + 1)

    def _shed(self, msgs: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
        """Last resort: drop whole old messages — what is left still stays verbatim.

        Preferred order: already-removed results → other tool results → old assistant
        answers without calls → old answers with calls (plus their results) → old user
        messages. Head and the last ``protect_last_n`` messages are never touched.
        """
        for _ in range(10_000):
            if estimate_messages_tokens_rough(msgs) <= budget:
                return msgs
            head_end = self._head_end(msgs)
            tail_start = max(head_end, len(msgs) - self.protect_last_n)
            results_of: dict[int, list[int]] = {}
            for p in self._pairs(msgs):
                if p["result"] is not None:
                    results_of.setdefault(p["msg"], []).append(p["result"])
            cand: list[tuple[int, int, list[int]]] = []
            for i in range(head_end, tail_start):
                msg = msgs[i]
                role = msg.get("role")
                if role == "tool":
                    cand.append((0 if msg.get("_jev_removed") else 1, i, []))
                elif role == "assistant":
                    cand.append((3 if _calls(msg) else 2, i, results_of.get(i, [])))
                elif role == "user":
                    cand.append((4, i, []))
            if not cand:
                return msgs
            _prio, idx, extra = sorted(cand, key=lambda t: (t[0], t[1]))[0]
            gone = {idx, *extra}
            msgs = [m for j, m in enumerate(msgs) if j not in gone]
        return msgs

    def _truncate(
        self, messages: list[dict[str, Any]], budget: int, arg_limit: int, text_limit: int
    ) -> None:
        """Deterministic cut, also used when the decision model stays silent."""
        for msg in messages:
            if msg.get("role") == "tool":
                body = _text_of(msg, 0)
                if len(body) > 1500:
                    msg["content"] = body[:900] + f" …[cut, was {len(body)}]… " + body[-500:]
                continue
            for call in _calls(msg):
                holder = call.get("function")
                if isinstance(holder, dict) and isinstance(holder.get("arguments"), str):
                    args = holder["arguments"]
                    if len(args) > arg_limit:
                        holder["arguments"] = args[:arg_limit] + "…"
                if isinstance(call.get("arguments"), str) and len(call["arguments"]) > arg_limit:
                    call["arguments"] = call["arguments"][:arg_limit] + "…"
            if msg.get("role") in ("user", "assistant") and isinstance(msg.get("content"), str):
                text = msg["content"]
                if text_limit and len(text) > text_limit:
                    msg["content"] = (
                        text[:text_limit]
                        + f" …[middle cut, was {len(text)}]… "
                        + text[-text_limit // 2 :]
                    )

    # ---- public surface ---------------------------------------------------------------

    def prune_tool_results_only(
        self, messages: list[dict[str, Any]], current_tokens: int | None = None
    ):
        """Light pass: only remove stale tool results, no full compaction.

        A live turn must not wait for the decision call, so this is skipped when it ran
        recently or when there is nothing much to decide. Errors never propagate: the turn
        matters more than the saving.
        """
        now = time.time()
        if now - getattr(self, "_last_light", 0.0) < 120:
            return messages, 0
        pairs = self._pairs(messages)
        if len(pairs) < 4:
            return messages, 0
        self._last_light = now
        try:
            return self._run(messages, budget=None, light=True)
        except Exception as exc:
            log.warning("light compaction skipped: %s", exc)
            return messages, 0

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int | None = None,
        focus_topic: str | None = None,
        force: bool = False,
        memory_context: str = "",
    ) -> list[dict[str, Any]]:
        before = estimate_messages_tokens_rough(messages)
        budget = int(self.threshold_tokens * 0.5) if self.threshold_tokens else before // 2
        try:
            out, _ = self._run(messages, budget=budget, light=False)
        except Exception as exc:
            log.warning("compaction failed (%s) — cutting deterministically", exc)
            out = copy.deepcopy(messages)
            self._truncate(
                out[self.protect_first_n : len(out) - self.protect_last_n], budget, 200, 2000
            )
        after = estimate_messages_tokens_rough(out)
        self.compression_count += 1
        log.info(
            "compaction: %d → %d tokens (threshold %d), decisions %s",
            before,
            after,
            self.threshold_tokens,
            self.last_run.get("verdicts", 0),
        )
        return out

    def _run(self, messages: list[dict[str, Any]], budget: int | None, light: bool):
        msgs = copy.deepcopy(messages)
        head_end = self._head_end(msgs)
        tail_start = max(head_end, len(msgs) - self.protect_last_n)
        all_pairs = self._pairs(msgs)
        # Candidates are pairs whose RESULT sits in the middle: the protected head and tail
        # are left alone, but a result in between can go.
        pairs = [
            p
            for p in all_pairs
            if (p["result"] is not None and head_end <= p["result"] < tail_start)
            or (p["result"] is None and head_end <= p["msg"] < tail_start)
        ]
        verdicts: dict[str, float] = {}
        state_info: dict[str, int] = {}
        if pairs and self._key_ok():
            state, state_info = self._fit_state(msgs)
            try:
                verdicts = self._ask(pairs, state)
            except Exception as exc:
                log.warning("decision model unreachable (%s) — cutting instead", exc)
                verdicts = {}
        dropped = self._drop(msgs, pairs, verdicts, head_end, tail_start)
        n_pruned = sum(dropped)
        self.last_run = {
            "pairs": len(pairs),
            "verdicts": len(verdicts),
            "dropped": dropped,
            "state": state_info,
            "budget": budget,
        }
        if budget is not None:
            # Escalating cuts: gentle first, then sharp, then whole messages.
            if estimate_messages_tokens_rough(msgs) > budget:
                self._truncate(msgs[head_end:tail_start], budget, 1000, 3000)
            if estimate_messages_tokens_rough(msgs) > budget:
                self._truncate(msgs[head_end:tail_start], budget, 200, 1200)
            if estimate_messages_tokens_rough(msgs) > budget:
                msgs = self._shed(msgs, budget)
        return msgs, n_pruned


def register(ctx) -> None:
    """Plugin entry point: hand the engine to the host."""
    ctx.register_context_engine(JevCompactionEngine())
