from __future__ import annotations

import json

from conftest import load_plugin


def test_verdict_maps_emoji_to_a_number():
    mod = load_plugin("feedback-reactions")
    assert mod._verdict(["👍"]) == 1
    assert mod._verdict(["🔥", "🙏"]) == 1
    assert mod._verdict(["👎"]) == -1
    assert mod._verdict(["🤬"]) == -1
    assert mod._verdict(["🤔"]) == 0  # a reaction, but neither verdict
    assert mod._verdict([]) is None  # not a reaction at all


def test_reaction_event_becomes_a_journal_row(hermes_home):
    mod = load_plugin("feedback-reactions")
    mod._on_platform_event(
        event={
            "event_type": "reaction",
            "platform": "telegram",
            "payload": {"chat_id": -100, "message_id": 7, "emojis": ["👍"]},
        }
    )
    rows = [
        json.loads(line)
        for line in (hermes_home / "data" / "feedback.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["verdict"] == 1 and row["counts"] is True
    assert row["reactions"] == ["👍"]
    assert row["chat_id"] == -100 and row["message_id"] == 7
    assert row["platform"] == "telegram"
    assert row["ts"].endswith("Z")


def test_negative_reaction_is_recorded_as_minus_one(hermes_home):
    mod = load_plugin("feedback-reactions")
    mod._on_platform_event(event={"event_type": "reaction", "payload": {"emojis": ["👎"]}})
    row = json.loads((hermes_home / "data" / "feedback.jsonl").read_text().splitlines()[0])
    assert row["verdict"] == -1
    assert row["platform"] == "unknown"


def test_other_events_are_left_alone(hermes_home):
    mod = load_plugin("feedback-reactions")
    mod._on_platform_event(event={"event_type": "message", "payload": {"emojis": ["👍"]}})
    mod._on_platform_event(event={"event_type": "reaction", "payload": {}})
    assert not (hermes_home / "data" / "feedback.jsonl").exists()


def test_a_broken_event_never_raises(hermes_home):
    mod = load_plugin("feedback-reactions")
    # half a payload, a string instead of a dict, a None: an observer must survive all three
    mod._on_platform_event(event={"event_type": "reaction", "payload": None})
    mod._on_platform_event(event="not a dict")
    mod._on_platform_event(event={"event_type": "reaction", "payload": {"emojis": [None, "👍"]}})
    rows = (hermes_home / "data" / "feedback.jsonl").read_text().strip().splitlines()
    assert len(rows) == 1  # the broken ones produced nothing


def test_registration_binds_the_platform_event_hook(fake_ctx):
    mod = load_plugin("feedback-reactions")
    mod.register(fake_ctx)
    assert list(fake_ctx.hooks) == ["gateway_platform_event"]
    assert fake_ctx.hooks["gateway_platform_event"] == [mod._on_platform_event]


def test_journal_path_follows_hermes_home(hermes_home, monkeypatch):
    mod = load_plugin("feedback-reactions")
    assert mod.journal_path() == hermes_home / "data" / "feedback.jsonl"
