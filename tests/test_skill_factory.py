from __future__ import annotations

import json

from conftest import load_plugin


def _post(mod, tool, status=None, session=None):
    mod._on_post_tool_call(tool_name=tool, status=status, session_id=session)


def test_events_are_recorded_and_the_window_is_bounded(hermes_home):
    mod = load_plugin("skill-factory")
    for _ in range(mod.MAX_EVENTS + 25):
        _post(mod, "read_file")
    events = json.loads((hermes_home / "plugin-data" / "skill-factory" / "events.json").read_text())
    assert len(events) == mod.MAX_EVENTS  # old events fall out, the file never grows
    assert events[-1]["tool"] == "read_file"


def test_a_recorder_that_breaks_does_not_break_the_session(hermes_home):
    mod = load_plugin("skill-factory")
    mod._on_post_tool_call(tool_name=None, status=None, session_id=None)  # must not raise
    events = json.loads((hermes_home / "plugin-data" / "skill-factory" / "events.json").read_text())
    assert events[-1]["tool"] == ""


def test_candidates_need_a_repetition(hermes_home):
    mod = load_plugin("skill-factory")
    _post(mod, "read_file")
    _post(mod, "terminal")
    _post(mod, "read_file")
    _post(mod, "terminal")
    patterns = {item["pattern"]: item["count"] for item in mod._candidates()}
    assert patterns["read_file"] == 2
    assert patterns["read_file -> terminal"] == 2
    assert all(count > 1 for count in patterns.values())


def test_a_one_off_is_not_a_candidate(hermes_home):
    mod = load_plugin("skill-factory")
    _post(mod, "write_file")
    assert mod._candidates() == []
    assert "one-off is not worth a skill" in mod._cmd_propose()


def test_propose_prints_the_candidates_and_a_work_order(hermes_home):
    mod = load_plugin("skill-factory")
    for _ in range(3):
        _post(mod, "terminal")
        _post(mod, "read_file")
    text = mod._cmd_propose()
    assert "terminal -> read_file ×3" in text
    assert "read the recorded events" in text.lower()
    assert "skill_manage(action='create'" in text


def test_propose_with_no_events_says_so(hermes_home):
    mod = load_plugin("skill-factory")
    assert "no tracked tool calls yet" in mod._cmd_propose()


def test_status_reports_window_and_errors(hermes_home):
    mod = load_plugin("skill-factory")
    _post(mod, "terminal")
    _post(mod, "terminal", status="timeout")
    text = mod._cmd_status()
    assert "tracked events : 2" in text
    assert "errors: 1" in text
    assert "terminal×2" in text


def test_save_list_and_clear(hermes_home):
    mod = load_plugin("skill-factory")
    assert "usage: /skill-factory-save" in mod._cmd_save("")  # no name, no acceptance
    assert "recorded proposal 'ship-it'" in mod._cmd_save("ship-it")
    assert "ship-it" in mod._cmd_list()
    assert "ship-it" in mod._cmd_status()
    _post(mod, "terminal")
    assert "window cleared" in mod._cmd_clear()
    assert (
        json.loads((hermes_home / "plugin-data" / "skill-factory" / "events.json").read_text())
        == []
    )


def test_state_is_written_atomically(hermes_home):
    mod = load_plugin("skill-factory")
    _post(mod, "terminal")
    state = hermes_home / "plugin-data" / "skill-factory"
    assert (state / "events.json").exists()
    assert not list(state.glob("*.tmp"))  # temp file is renamed, never left behind


def test_registration_binds_hook_and_commands(fake_ctx):
    mod = load_plugin("skill-factory")
    mod.register(fake_ctx)
    assert list(fake_ctx.hooks) == ["post_tool_call"]
    assert set(fake_ctx.commands) == {
        "skill-factory-propose",
        "skill-factory-status",
        "skill-factory-list",
        "skill-factory-save",
        "skill-factory-clear",
    }
    assert fake_ctx.commands["skill-factory-save"]["args_hint"] == "<skill-name>"
