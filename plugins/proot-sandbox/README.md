# proot-sandbox

Runs the agent's shell commands inside a sandbox that cannot read your secrets.

An agent with a terminal tool has your home directory. That is convenient right up to the
moment a command reads `~/.hermes/.env`, a session transcript, or a provider key. This
plugin makes the terminal a terminal **inside a box**: everything happens under
[proot](https://proot-me.github.io/) — userspace only, no root, no kernel namespaces, no
privileged helper — so it runs fine inside a container or on a VPS where you are a normal
user.

## What the command can and cannot see

| Visible | Not visible |
| --- | --- |
| `/workspace` (the only writable tree) | `$HERMES_HOME` — sessions, memories, `config.yaml`, `.env` |
| `/usr`, `/bin`, `/lib*`, `/sbin` (read-only) | profile directories and their keys |
| the `/etc` files a shell needs | provider API keys (stripped from the environment) |
| the network | the real home directory (a scratch home is mounted instead) |

Because the Hermes home is not mounted at all, a command cannot read what is not there —
this is a mount decision, not a filter that can be talked around.

## Install

```sh
cp -r proot-sandbox "$HERMES_HOME/plugins/"
hermes plugins doctor          # reports whether a proot binary was found
```

`proot` itself is the only external requirement: a distribution package, a static build, or
a bundled one pointed at with `HERMES_PROOT_BIN` (plus `HERMES_PROOT_LIB` if it carries its
own libraries).

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `HERMES_PROOT_BIN` | `proot` from `PATH` | proot binary |
| `HERMES_PROOT_LIB` | — | value added to `LD_LIBRARY_PATH` for a bundled proot |
| `HERMES_SANDBOX_WORKSPACE` | `$HERMES_HOME/coder-workspace` | host directory mounted as `/workspace` |
| `HERMES_SANDBOX_HOME` | `/tmp/sb-home` | host directory mounted as the sandbox home |
| `HERMES_SANDBOX_TMP` | `$HERMES_HOME/coder-spool` | host directory mounted as `/tmp` |
| `HERMES_SANDBOX_STRIP_ENV` | — | extra variable names to strip, comma-separated |

## Two details that cost us a day each

**`TMPDIR` must share a filesystem with `/workspace`.** A temp file on tmpfs cannot be
renamed into the workspace, so an atomic write (temp + rename) fails with `EXDEV` and the
file stays empty. `TMPDIR` is pinned to `/tmp`, which is a bind mount of the spool
directory on the same disk.

**A timeout must return, not hang.** A command that exceeds its timeout comes back as exit
code `124` with a message, so the agent tool sees a result instead of a dead terminal.

## Sandboxed, not bulletproof

proot is not a container: it intercepts syscalls in userspace, and the process still runs
as your user. Treat it as "the agent cannot accidentally read my secrets", not as a
defence against a determined attacker with code execution. If you need the stronger
version, put the same provider in front of a real container runtime.
