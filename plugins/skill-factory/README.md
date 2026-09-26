# skill-factory

Turns a repeated workflow into a skill, instead of guessing one.

The plugin listens to `post_tool_call` and keeps a bounded window of the last 500 tool
calls of the session. Then it counts single tools and tool pairs. Anything that happened
more than once is a **candidate workflow** — repetition is the evidence that a procedure
is worth writing down.

```
/skill-factory-propose
```

prints the candidates and hands the agent a work order: read the recorded events, pick
**one** reusable procedure, write it with the skill tooling, then record the name. The
command deliberately does not try to write the skill itself: naming the repetition is the
useful part, inventing a procedure from nothing is how you get skills nobody keeps.

| Command | What it does |
| --- | --- |
| `/skill-factory-propose` | Show candidates and the work order for the agent |
| `/skill-factory-status` | Tracked events, top tools, error count, saved proposals |
| `/skill-factory-list` | List proposals recorded so far |
| `/skill-factory-save <name>` | Record an accepted proposal |
| `/skill-factory-clear` | Empty the event window |

State lives in `$HERMES_HOME/plugin-data/skill-factory/` and is written atomically
(temp file + rename), so an interrupted write cannot leave a half-empty window behind.

## Credit

The idea comes from [`Romanescu11/hermes-skill-factory`](https://github.com/Romanescu11/hermes-skill-factory),
which targets an older plugin API. This is a re-implementation on the current contract
(`register(ctx)` with `ctx.register_hook` / `ctx.register_command`), so the plugin loads
and `hermes plugins doctor` stays clean.
