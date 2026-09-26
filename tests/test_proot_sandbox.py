from __future__ import annotations

from conftest import load_plugin


def test_the_workspace_is_mounted_and_the_hermes_home_is_not(hermes_home):
    mod = load_plugin("proot-sandbox")
    argv, inner_cwd = mod.build_argv("ls -la")
    joins = [argv[i + 1] for i, item in enumerate(argv) if item == "-b"]

    assert f"{mod._workspace()}:/workspace" in joins
    assert inner_cwd == "/workspace"
    # the home root itself (config, sessions, memories, keys) is never mounted — only the two
    # sandbox directories that deliberately live underneath it
    sources = [j.split(":")[0] for j in joins if str(hermes_home) in j]
    assert str(hermes_home) not in sources
    assert set(sources) <= {str(mod._workspace()), str(mod._scratch_tmp())}
    for forbidden in ("config.yaml", "sessions", "memories", "profiles", ".env", "plugins"):
        assert not [j for j in sources if j.endswith(forbidden)]


def test_provider_api_keys_are_stripped():
    mod = load_plugin("proot-sandbox")
    for key in ("OPENROUTER_API_KEY", "TELEGRAM_BOT_TOKEN", "GH_TOKEN", "TYPESAFE_API_KEY"):
        assert key in mod.STRIP_ENV
    assert mod.ProotSandboxProvider().strip_env_keys == mod.STRIP_ENV


def test_the_scratch_home_inside_is_named_after_a_user(hermes_home, monkeypatch):
    mod = load_plugin("proot-sandbox")
    monkeypatch.setenv("HERMES_SANDBOX_USER", "sandboxer")
    argv, _ = mod.build_argv("echo $HOME")
    joins = [argv[i + 1] for i, item in enumerate(argv) if item == "-b"]
    assert f"{mod._scratch_home()}:/home/sandboxer" in joins
    assert mod._sandbox_home_inner() == "/home/sandboxer"


def test_paths_follow_hermes_home(hermes_home, monkeypatch):
    mod = load_plugin("proot-sandbox")
    assert mod._workspace() == hermes_home / "coder-workspace"
    assert mod._scratch_tmp() == hermes_home / "coder-spool"
    monkeypatch.setenv("HERMES_SANDBOX_WORKSPACE", "/srv/sandbox")
    assert mod._workspace().as_posix() == "/srv/sandbox"


def test_a_host_cwd_inside_the_workspace_is_mapped_inside(hermes_home):
    mod = load_plugin("proot-sandbox")
    _argv, inner_cwd = mod.build_argv("pwd", cwd=str(mod._workspace() / "sub"))
    assert inner_cwd == "/workspace/sub"
    # a host path that is not in the sandbox must not leak in as a working directory
    _argv, other = mod.build_argv("pwd", cwd="/etc")
    assert other == "/workspace"


def test_a_timeout_comes_back_as_exit_code_124(monkeypatch):
    mod = load_plugin("proot-sandbox")

    def fake_run(*_args, **_kwargs):
        raise mod.subprocess.TimeoutExpired(cmd="proot", timeout=1)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    result = mod.ProotSandboxEnvironment(cwd="/workspace", timeout=1).execute("sleep 99")
    assert result["exit_code"] == 124
    assert "timed out" in result["output"]


def test_output_and_returncode_are_what_the_host_reads(monkeypatch):
    mod = load_plugin("proot-sandbox")

    class Done:
        stdout = "out"
        stderr = "err"
        returncode = 3

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: Done())
    result = mod.ProotSandboxEnvironment(cwd="/workspace", timeout=5).execute("false")
    assert result["output"] == "outerr"
    assert result["returncode"] == 3 and result["exit_code"] == 3


def test_availability_depends_on_the_proot_binary(monkeypatch, tmp_path):
    mod = load_plugin("proot-sandbox")
    provider = mod.ProotSandboxProvider()
    monkeypatch.setenv("HERMES_PROOT_BIN", str(tmp_path / "nope"))
    assert provider.is_available() is False
    assert provider.doctor_checks()[0][0] is False

    binary = tmp_path / "proot"
    binary.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("HERMES_PROOT_BIN", str(binary))
    assert provider.is_available() is True
    assert provider.doctor_checks()[0][0] is True


def test_registration_hands_over_a_provider(fake_ctx):
    mod = load_plugin("proot-sandbox")
    mod.register(fake_ctx)
    assert len(fake_ctx.terminal_providers) == 1
    assert fake_ctx.terminal_providers[0].name == "proot-sandbox"
    assert fake_ctx.terminal_providers[0].is_container is True
