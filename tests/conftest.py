"""Load plugins by path.

Plugin directories are named the way the host expects (`feedback-reactions`), which is not
an importable module name, so every plugin is loaded through importlib with an explicit
search location — that also lets `from . import typesafe` work inside a plugin package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"


def load_plugin(name: str):
    path = PLUGINS / name / "__init__.py"
    mod_name = "pack_" + name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(
        mod_name, path, submodule_search_locations=[str(path.parent)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    """Records what a plugin asks the host for, so registration can be asserted."""

    def __init__(self):
        self.hooks = {}
        self.commands = {}
        self.engines = []
        self.terminal_providers = []

    def register_hook(self, name, fn):
        self.hooks.setdefault(name, []).append(fn)

    def register_command(self, name, fn, description="", args_hint=""):
        self.commands[name] = {"fn": fn, "description": description, "args_hint": args_hint}

    def register_context_engine(self, engine):
        self.engines.append(engine)

    def register_terminal_environment_provider(self, provider):
        self.terminal_providers.append(provider)


@pytest.fixture
def fake_ctx():
    return FakeCtx()


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Point a plugin at a throwaway home instead of the real one."""
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home
