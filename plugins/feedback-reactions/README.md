# feedback-reactions

Turns chat reactions into rows a report can read.

A 👍 is one tap and no words — the cheapest signal a person can give, and most agents
throw it away. This plugin catches the reaction event the host already forwards and
appends a line to `$HERMES_HOME/data/feedback.jsonl`, the same journal text verdicts
go into.

```json
{"ts":"2026-09-26T09:12:44Z","event":"reaction","platform":"telegram","chat_id":-100123,
 "message_id":42,"reactions":["👍"],"verdict":1,"counts":true,"source":"chat reaction"}
```

- `verdict` is `1` for positive emoji, `-1` for negative, `0` for a reaction that is
  neither (recorded, but not counted either way).
- `counts: true` marks the row as countable, so a weekly review that sums text verdicts
  can sum these too without a special case.

The hook swallows its own errors: an observer must never be able to break the message
path. If the journal cannot be written, the reaction is simply lost.

Install:

```sh
cp -r feedback-reactions "$HERMES_HOME/plugins/"
hermes plugins doctor
```

No configuration, no dependencies, no network calls.
