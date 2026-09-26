# jev_compact

Compaction that cuts, but never rewrites.

Every other context engine summarises: the model reads your history and writes a shorter
version of it. Details drift, and a number you were arguing about quietly becomes a different
number. This engine does not rewrite anything. One fast decision call looks at the whole
conversation and answers a single question per tool call — is this result still needed later?
What it calls stale is removed. Everything that stays stays **verbatim**.

## How it works

1. **Pairs, not messages.** A tool call is useless without its result, so they are treated as
   one unit: `_pairs()` matches each call to its result by `tool_call_id`.
2. **One decision call per batch.** Up to 30 calls go into a single request, with the
   conversation compressed into a short state: tool results are replaced by
   `(ok, N chars, hidden)`, arguments and assistant text are kept up to a limit. The view
   shrinks itself until it fits rather than being cut off mid-sentence.
3. **Cutting is conservative.** A whole pair goes only when both halves are in the protected
   middle *and* the assistant message carries no text of its own. If the assistant wrote a
   sentence, the sentence keeps its call and the result is shrunk to
   `…[removed by context engine, N chars]…`. Losing a sentence would be rewriting the
   conversation, which is the thing this engine exists to avoid.
4. **Escalation, if the budget is still not met.** Gentle truncation of long results →
   sharper truncation → dropping whole old messages, in a fixed order (already-removed
   results, other results, old answers, old answers with calls, old user messages). The head
   and the last messages are never touched.
5. **Everything is logged.** Each verdict lands in
   `$HERMES_HOME/data/jev_compact_decisions.jsonl` as
   `{"ts": …, "tool": "read_file", "drop_p": 0.91, "kept": false}`, so you can audit what was
   thrown away instead of trusting it.

## Fail-open, on purpose

A context engine sits inside a live turn. If TypeSafe is unreachable, the answer is missing,
or the key is not set at all, the engine degrades to a deterministic cut and the turn
continues. Silence never deletes anything: a pair with no verdict is kept. The whole
`prune_tool_results_only()` pass is also throttled to at most once every two minutes, so a
cheap question is not asked in every step of a long task.

## Install

```sh
cp -r jev_compact "$HERMES_HOME/plugins/"
export TYPESAFE_API_KEY=...        # the same key the TypeSafe Jev plugin uses
hermes plugins doctor
```

Configuration lives in `config.yaml` under `compression:` — `threshold_tokens` (default
250000), `protect_first_n`, `protect_last_n`, `drop_probability` (default 0.75) — and every
key can be overridden with `COMPRESSION_<KEY>` in the environment.

## Cost

One request per compaction round, with the answer per tool call. `tries=1` and a 25-second
timeout are deliberate: this runs inside a turn, so it either answers quickly or gets out of
the way.

## Credit

The idea — and the observation that dropping stale calls beats summarising — comes from
[`tamaratran/fast-jev-compaction`](https://github.com/tamaratran/fast-jev-compaction) (MIT).
This is an independent implementation against the Hermes plugin API and the TypeSafe System
One endpoint; no code was copied.
