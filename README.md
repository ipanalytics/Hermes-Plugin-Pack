# Hermes Plugin Pack

Four plugins we run every day on a Hermes Agent box. They are small on purpose: each plugin is a
directory with a manifest and one or two Python files, no third-party dependencies, and does one
job that the default agent does not do.

Most plugin examples you find are demos. These are the pieces that survived months of daily
use — a feedback journal, a skill drafter, a sandboxed shell, and a context compactor that
never rewrites your transcript.

*Русская версия: [README.ru.md](README.ru.md)*

---

## What is inside

| Plugin | Problem it solves | Hook / surface | Needs |
| --- | --- | --- | --- |
| `feedback-reactions` | Your thumbs up/down in chat disappears into the void | `gateway_platform_event` | nothing |
| `skill-factory` | The same workflow is redone by hand every week | `post_tool_call` + 5 slash commands | nothing |
| `proot-sandbox` | Agent shell commands can read your secrets and home directory | terminal environment provider | a `proot` build |
| `jev_compact` | Compaction rewrites the whole transcript and loses detail | context engine | TypeSafe API key |

---

### 1. `feedback-reactions` — reactions become a data set

Telegram reactions arrive as platform events; the host only forwards them for authorized
people, so no manual filtering is needed. The plugin maps the emoji set to a verdict and
appends one JSON line to `$HERMES_HOME/data/feedback.jsonl` — the same file text verdicts
already go to, so an existing weekly review keeps working without changes.

```text
{"ts":"2026-09-26T09:12:44Z","event":"reaction","platform":"telegram","chat_id":...,"message_id":...,
 "reactions":["👍"],"verdict":1,"counts":true,"source":"chat reaction"}
```

Cool emoji count as positive, angry ones as negative, everything else is recorded as neutral.
The hook swallows its own errors on purpose: an observer must never be able to take the gateway
down.

### 2. `skill-factory` — turn a repeated workflow into a skill

Passively records every tool call of the session (`post_tool_call`) into a bounded window of
500 events, then counts single tools and tool pairs. Anything that happened more than once is a
*candidate workflow* — that is the signal a procedure deserves to be written down.

`/skill-factory-propose` does not try to be clever. It prints the candidates and hands the agent
a concrete work order: read the recorded events, pick **one** reusable procedure, write it with
the skill tooling, then record the name. Guessing a skill from nothing produces noise; naming the
repetition first produces a skill you keep.

Commands: `propose`, `status`, `list`, `save <name>`, `clear`. State lives in
`$HERMES_HOME/plugin-data/skill-factory/`, written atomically (temp file + rename).

Credited: the same idea as `Romanescu11/hermes-skill-factory`, re-implemented on the current
plugin contract (`register(ctx)` + `ctx.register_hook` / `ctx.register_command`) so that
`hermes plugins doctor` passes and the surface actually binds.

### 3. `proot-sandbox` — a shell that cannot see your secrets

A terminal environment backend: commands run under `proot` (userspace, no root, no kernel
features required) with `/workspace` as the only writable project tree. The agent's home —
`sessions`, `memories`, `config.yaml`, `.env`, profile keys — is simply not mounted, so a
command cannot read what is not there. The host user's home is replaced by a scratch home.

The provider declares itself as a container, strips provider API keys from the environment
before the command runs, and reports availability through the normal doctor checks. A command
timeout returns exit code 124 instead of hanging the terminal tool.

Paths are read from `$HERMES_HOME`; the only external requirement is a `proot` binary.

### 4. `jev_compact` — compaction that cuts, but never rewrites

Normal compaction summarises: the model re-writes your history, and details quietly change.
Here a small, fast decision model scores each tool call in the transcript and answers one
question per call — keep, shorten, or drop. Stale calls and their results are removed;
everything that stays stays **verbatim**, so nothing is paraphrased, invented, or merged.

The decision costs one cheap call, every decision is logged to
`$HERMES_HOME/data/jev_compact_decisions.jsonl` so you can audit what was thrown away, and if
the decision model is unreachable the plugin fails open — the transcript is left untouched.

Needs a TypeSafe API key (`requires_env: TYPESAFE_API_KEY`). Idea credited to
`tamaratran/fast-jev-compaction` (MIT); this is an independent implementation on the Hermes
plugin API, not a copy.

---

## Not in this pack, on purpose

We also run three plugins by other authors. They are MIT-licensed and maintained upstream, so we
link them instead of vendoring somebody else's code:

- [`hermes-jev`](https://github.com/kerpopule/hermes-jev-skills) — Steve Darlow (MIT): TypeSafe
  decisions for routing, memory filtering, action choice.
- [`hermes-handoff`](https://github.com/kerpopule/hermes-jev-skills) — Steve Darlow (MIT):
  deliberately closing a session and opening the next one with a capsule.
- [`typesafe-skill-router`](https://github.com/DECRUX9812/typesafe-skill-router) — Ritesh Patel
  (MIT): names the one skill worth loading before the model call.

Copying a maintained plugin into your own tree means you inherit the maintenance and lose the
updates. Install upstream, pin the version, and keep your own pack for things you actually wrote.

---

## Install

```sh
# Hermes Agent keeps plugins next to its home directory
mkdir -p "${HERMES_HOME:-$HOME/.hermes}/plugins"
cp -r plugins/* "${HERMES_HOME:-$HOME/.hermes}/plugins/"

hermes plugins doctor     # will complain about missing TYPESAFE_API_KEY for jev_compact
```

Drop the plugin you do not need. `proot-sandbox` additionally wants `HERMES_PROOT_BIN` to point at
a proot binary if it is not on `PATH`.

---

## Writing your own plugin: what we learned

A Hermes plugin is a directory with `plugin.yaml` and an `__init__.py` exposing `register(ctx)`:

```python
def register(ctx):
    ctx.register_hook("post_tool_call", my_hook)  # observe
    ctx.register_command("my-command", my_command, description="…")  # act
```

Rules that keep a plugin from becoming a liability:

1. **Never take the gateway down.** Wrap every hook body in `try/except` and return quietly. A
   broken observer must not break the message path.
2. **Bounded state.** A JSON file with a fixed window beats a database you have to migrate.
3. **Stdlib first.** None of the four plugins here imports anything outside the standard library
   except `requests` in the host itself. A dependency is a promise to maintain it.
4. **Declare what you need.** `requires_env` for secrets, `config_schema` for knobs — the host can
   then tell the user what is missing instead of crashing at import time.
5. **Write down credit.** If the idea is somebody else's, say so in the header and link it.

Hooks used across this pack: `gateway_platform_event`, `post_tool_call`, `pre_llm_call`, plus the
terminal environment provider surface for backends.

---

## Status and testing

```sh
python -m pytest -q      # 39 passed
python -m ruff check .   # clean
```

| Plugin | Tests | Covered |
| --- | --- | --- |
| `feedback-reactions` | 7 | verdict mapping, journal row shape, ignored events, broken payloads, hook binding |
| `skill-factory` | 10 | bounded window, repetition counting, propose output, save/list/clear, atomic state |
| `proot-sandbox` | 9 | bind table (home root not mounted), stripped keys, `$HERMES_HOME` paths, sandbox home name, timeout → 124 |
| `jev_compact` | 13 | pair matching, drop with orphaned result, shrink instead of delete, protected head/tail, answer parsing, audit log, no-key and API-failure fallbacks |

The tests load the plugins the way the host does (by path, with a fake `ctx`) and never touch
the network: the TypeSafe call is stubbed, `subprocess` is faked. Everything the plugins write
goes to a throwaway `$HERMES_HOME` under `tmp_path`.

What the tests do **not** prove: that the plugins behave the same inside a live Hermes process.
That part is still "runs every day in production", which is how the bugs that are fixed here
were found in the first place.

---

## Requirements, license, privacy

- Hermes Agent with the plugin API (`manifest_version: 2` era), Python 3.11+.
- MIT.
- No telemetry of any kind. Nothing leaves the machine except the model calls the host already
  makes; `jev_compact` talks to TypeSafe because that is the whole point of it.

---

## Keywords

Hermes Agent plugins, agent sandboxing without root, proot, LLM agent context compaction, agent
skill drafting, feedback loop from chat reactions, self-hosted AI agent tooling.
