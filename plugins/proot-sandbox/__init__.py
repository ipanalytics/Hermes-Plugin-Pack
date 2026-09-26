"""proot-sandbox — run the agent's shell in a sandbox that cannot see your secrets.

A terminal environment backend for Hermes Agent. Isolation is done with proot: no root,
no kernel namespaces, no privileged helper — so it works inside a container or a VPS where
you are just a normal user.

What a command gets:
  * ``/workspace`` — the only writable project tree (default ``$HERMES_HOME/coder-workspace``);
  * a scratch home instead of the real one;
  * the system read-only: /usr /bin /lib* /sbin plus the few /etc files a shell needs
    (resolv.conf, hosts, passwd, group, ssl, ld.so.cache).

What a command does NOT get:
  * ``$HERMES_HOME`` — sessions, memories, config.yaml, .env and profile keys are simply not
    mounted, so a command cannot read what is not there;
  * provider API keys — the usual suspects are removed from the environment before the
    command runs (extend with ``HERMES_SANDBOX_STRIP_ENV``).

Everything is configurable through the environment:
  HERMES_HOME                 base directory (default: ~/.hermes)
  HERMES_PROOT_BIN            proot binary (default: proot from PATH)
  HERMES_PROOT_LIB            extra LD_LIBRARY_PATH entry for a bundled proot
  HERMES_SANDBOX_WORKSPACE    host path mounted as /workspace
  HERMES_SANDBOX_HOME         host path mounted as the sandbox home
  HERMES_SANDBOX_TMP          host path mounted as /tmp
  HERMES_SANDBOX_STRIP_ENV    comma-separated extra variable names to strip
"""

from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

try:  # inside a Hermes host
    from agent.terminal_env_provider import TerminalEnvironmentProvider
except ImportError:  # standalone import (unit tests, docs) — duck-typed base

    class TerminalEnvironmentProvider:  # type: ignore[no-redef]
        """Minimal stand-in so the module can be imported without a host."""

        name = "base"
        display_name = "base"
        is_remote = False
        is_container = False
        skip_container_guards = False

        description = ""
        cache_path_base = "~/.hermes"
        strip_env_keys: frozenset[str] = frozenset()

        def is_available(self) -> bool:
            return False

        def probe(self) -> dict:
            return {"ok": False, "detail": "no host"}

        def doctor_checks(self) -> list:
            return []


STRIP_ENV = frozenset(
    {
        "OPENROUTER_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "KIMI_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "MISTRAL_API_KEY",
        "TYPESAFE_API_KEY",
        "ELEVENLABS_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    }
)


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _workspace() -> Path:
    raw = os.environ.get("HERMES_SANDBOX_WORKSPACE")
    return Path(raw) if raw else hermes_home() / "coder-workspace"


def _scratch_home() -> Path:
    return Path(os.environ.get("HERMES_SANDBOX_HOME") or "/tmp/sb-home")


def _scratch_tmp() -> Path:
    raw = os.environ.get("HERMES_SANDBOX_TMP")
    return Path(raw) if raw else hermes_home() / "coder-spool"


def _proot_bin() -> str:
    return os.environ.get("HERMES_PROOT_BIN") or "proot"


def _sandbox_user() -> str:
    """Name the scratch home after a real user, so tools that print $HOME look sane."""
    return os.environ.get("HERMES_SANDBOX_USER") or getpass.getuser() or "sandbox"


def _sandbox_home_inner() -> str:
    return f"/home/{_sandbox_user()}"


def _binds() -> list[tuple[str, str]]:
    """The whole sandbox in one table: what is visible, and where it lands inside."""
    return [
        ("-b", f"{_workspace()}:/workspace"),
        ("-b", f"{_scratch_home()}:{_sandbox_home_inner()}"),
        ("-b", f"{_scratch_tmp()}:/tmp"),
        ("-b", "/usr"),
        ("-b", "/bin"),
        ("-b", "/lib"),
        ("-b", "/lib64"),
        ("-b", "/sbin"),
        ("-b", "/etc/alternatives"),
        ("-b", "/etc/ld.so.cache"),
        ("-b", "/etc/ld.so.conf"),
        ("-b", "/etc/ld.so.conf.d"),
        ("-b", "/etc/passwd"),
        ("-b", "/etc/group"),
        ("-b", "/etc/nsswitch.conf"),
        ("-b", "/etc/resolv.conf"),
        ("-b", "/etc/hosts"),
        ("-b", "/etc/ssl"),
        ("-b", "/etc/ca-certificates"),
    ]


def build_argv(command: str, cwd: str | None = None) -> tuple[list[str], str]:
    """Command -> (argv for proot, path the command actually starts in).

    Kept as a separate function on purpose: the argv is the security boundary, so it is
    worth being able to read it in a test without running a sandbox.
    """
    inner_cwd = "/workspace"
    if cwd and str(cwd).startswith("/workspace"):
        inner_cwd = str(cwd)
    elif cwd and str(cwd).startswith(str(_workspace())):
        inner_cwd = "/workspace" + str(cwd)[len(str(_workspace())) :]
    argv: list[str] = [_proot_bin()]
    for flag, bind in _binds():
        argv += [flag, bind]
    argv += ["-w", inner_cwd, "/bin/bash", "-c", command]
    return argv, inner_cwd


class ProotSandboxEnvironment:
    """Duck-typed terminal environment: execute() + cleanup()."""

    def __init__(self, cwd, timeout, task_id="default", **kwargs):
        self.cwd = "/workspace"
        self.timeout = timeout or 300
        self.task_id = task_id
        for directory in (_scratch_home(), _scratch_tmp(), _workspace()):
            os.makedirs(directory, exist_ok=True)

    def execute(self, command, timeout=None, cwd=None, stdin_data=None, **kwargs):
        timeout = timeout or self.timeout
        argv, _ = build_argv(command, cwd)
        env = dict(os.environ)
        env["HOME"] = _sandbox_home_inner()
        lib = os.environ.get("HERMES_PROOT_LIB")
        if lib:
            env["LD_LIBRARY_PATH"] = lib
        # TMPDIR must sit on the same filesystem as /workspace: a temp file on tmpfs cannot
        # be renamed into the workspace, and atomic writes would fail with EXDEV.
        env["TMPDIR"] = "/tmp"
        for key in STRIP_ENV | {
            k.strip()
            for k in (os.environ.get("HERMES_SANDBOX_STRIP_ENV") or "").split(",")
            if k.strip()
        }:
            env.pop(key, None)
        try:
            proc = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd="/",
                input=stdin_data,
                stdin=None if stdin_data is not None else subprocess.DEVNULL,
            )
            # returncode is the key the host reads; exit_code is kept for helper scripts.
            return {
                "output": (proc.stdout or "") + (proc.stderr or ""),
                "returncode": proc.returncode,
                "exit_code": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"output": f"[sandbox] command timed out after {timeout}s", "exit_code": 124}
        except Exception as exc:
            return {"output": f"[sandbox] error: {exc}", "exit_code": 1}

    def cleanup(self):
        """Nothing to clean: the sandbox is a set of directories, not a container."""


class ProotSandboxProvider(TerminalEnvironmentProvider):
    name = "proot-sandbox"
    display_name = "Proot Sandbox (userspace)"
    is_remote = False
    is_container = True
    skip_container_guards = False

    @property
    def description(self) -> str:
        return (
            "Userspace proot sandbox: commands run inside /workspace, "
            "the Hermes home and its secrets are not mounted."
        )

    @property
    def cache_path_base(self) -> str:
        return str(hermes_home())

    @property
    def strip_env_keys(self) -> frozenset[str]:
        return STRIP_ENV

    def is_available(self) -> bool:
        binary = _proot_bin()
        if os.path.sep in binary:
            return os.path.isfile(binary)
        for directory in (os.environ.get("PATH") or "").split(os.pathsep):
            if directory and os.path.isfile(os.path.join(directory, binary)):
                return True
        return False

    def probe(self) -> dict:
        return {"ok": self.is_available(), "detail": _proot_bin()}

    def doctor_checks(self) -> list:
        ok = self.is_available()
        return [
            (
                ok,
                "proot binary",
                _proot_bin() if ok else f"{_proot_bin()} not found — set HERMES_PROOT_BIN",
            )
        ]

    def create_environment(
        self, *, cwd, timeout, task_id="default", image=None, container_config=None, **kwargs
    ):
        return ProotSandboxEnvironment(cwd, timeout, task_id)


def register(ctx) -> None:
    ctx.register_terminal_environment_provider(ProotSandboxProvider())
