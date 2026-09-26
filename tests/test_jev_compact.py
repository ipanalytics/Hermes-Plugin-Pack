from __future__ import annotations

import json

from conftest import load_plugin


def _engine(mod, protect_first=2, protect_last=1):
    engine = mod.JevCompactionEngine()
    engine.protect_first_n = protect_first
    engine.protect_last_n = protect_last
    return engine


def _messages(assistant_text=""):
    return [
        {"role": "system", "content": "you are a careful agent"},
        {"role": "user", "content": "read the config and tell me what is wrong"},
        {
            "role": "assistant",
            "content": assistant_text,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"/etc/app.conf"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "tool_name": "read_file",
            "content": "HOST=localhost\nPORT=8080",
        },
        {"role": "assistant", "content": "the port is wrong"},
        {"role": "user", "content": "fix it"},
    ]


def test_pairs_match_calls_with_their_results():
    mod = load_plugin("jev_compact")
    pairs = _engine(mod)._pairs(_messages())
    assert len(pairs) == 1
    assert pairs[0]["msg"] == 2 and pairs[0]["result"] == 3
    assert pairs[0]["name"] == "read_file" and "app.conf" in pairs[0]["args"]


def test_a_call_the_model_dropped_goes_with_its_orphaned_result():
    mod = load_plugin("jev_compact")
    engine = _engine(mod)
    msgs = _messages()
    pairs = engine._pairs(msgs)
    dropped = engine._drop(msgs, pairs, {pairs[0]["key"]: 0.9}, head_end=2, tail_start=5)
    assert dropped == (0, 1)
    assert msgs[2]["tool_calls"] is None
    assert msgs[3]["content"] == "" and msgs[3]["_jev_removed"] is True


def test_when_the_assistant_also_wrote_something_the_result_is_shrunk_not_deleted():
    mod = load_plugin("jev_compact")
    engine = _engine(mod)
    msgs = _messages(assistant_text="let me open the config first")
    pairs = engine._pairs(msgs)
    dropped = engine._drop(msgs, pairs, {pairs[0]["key"]: 0.9}, head_end=2, tail_start=5)
    assert dropped == (1, 0)
    assert "removed by context engine" in msgs[3]["content"]
    assert "PORT=8080" not in msgs[3]["content"]  # the body is gone, not paraphrased
    assert msgs[2]["tool_calls"]  # the sentence keeps its call


def test_a_low_probability_changes_nothing():
    mod = load_plugin("jev_compact")
    engine = _engine(mod)
    msgs = _messages()
    pairs = engine._pairs(msgs)
    before = json.dumps(msgs, sort_keys=True)
    assert engine._drop(msgs, pairs, {pairs[0]["key"]: 0.4}, head_end=2, tail_start=5) == (0, 0)
    assert json.dumps(msgs, sort_keys=True) == before


def test_protected_head_and_tail_are_never_touched():
    mod = load_plugin("jev_compact")
    engine = _engine(mod, protect_first=2, protect_last=2)
    msgs = _messages()
    pairs = engine._pairs(msgs)
    # head_end=2 and tail_start=3: the result at index 3 is inside the protected tail
    assert engine._drop(msgs, pairs, {pairs[0]["key"]: 0.99}, head_end=2, tail_start=3) == (0, 0)
    assert msgs[3]["content"].startswith("HOST=localhost")
    assert msgs[2]["tool_calls"]


def test_a_call_in_the_protected_head_is_left_alone():
    mod = load_plugin("jev_compact")
    engine = _engine(mod, protect_first=4, protect_last=1)
    msgs = _messages()
    pairs = engine._pairs(msgs)
    # the call (2) and its result (3) both sit inside a head of four messages
    assert engine._drop(msgs, pairs, {pairs[0]["key"]: 0.99}, head_end=4, tail_start=5) == (0, 0)
    assert msgs[3]["content"].startswith("HOST=localhost")


def test_ask_parses_the_answer_and_logs_the_decision(hermes_home, monkeypatch):
    mod = load_plugin("jev_compact")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    engine = _engine(mod)
    pairs = engine._pairs(_messages())

    def fake_ask(state, questions, **kwargs):
        assert "read_file" in questions["t0"]["instructions"]
        return {
            "answers": {"t0": {"choice": "drop", "probabilities": {"drop": 0.91, "keep": 0.09}}}
        }, 0.4

    monkeypatch.setattr(mod.typesafe, "ask", fake_ask)
    verdicts = engine._ask(pairs, "state text")
    assert verdicts == {pairs[0]["key"]: 0.91}
    row = json.loads(
        (hermes_home / "data" / "jev_compact_decisions.jsonl").read_text().splitlines()[0]
    )
    assert row == {"ts": row["ts"], "tool": "read_file", "drop_p": 0.91, "kept": False}


def test_an_unanswered_pair_is_kept(hermes_home, monkeypatch):
    mod = load_plugin("jev_compact")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    engine = _engine(mod)
    pairs = engine._pairs(_messages())
    monkeypatch.setattr(mod.typesafe, "ask", lambda *a, **k: ({"answers": {}}, 0.1))
    assert engine._ask(pairs, "state") == {}  # silence never deletes anything


def test_without_a_key_the_engine_still_compresses(hermes_home, monkeypatch):
    mod = load_plugin("jev_compact")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    engine = _engine(mod, protect_first=1, protect_last=1)
    assert engine._key_ok() is False
    engine.threshold_tokens = 50
    msgs = [*_messages(), {"role": "tool", "tool_call_id": "x", "content": "x" * 4000}]
    out = engine.compress(msgs)
    assert isinstance(out, list) and out
    assert out[0]["role"] == "system"  # head stays where it was


def test_an_api_failure_falls_back_to_a_cut(hermes_home, monkeypatch):
    mod = load_plugin("jev_compact")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    engine = _engine(mod, protect_first=1, protect_last=1)
    engine.threshold_tokens = 50

    def explode(*_a, **_k):
        raise RuntimeError("TypeSafe is down")

    monkeypatch.setattr(mod.typesafe, "ask", explode)
    msgs = [*_messages(), {"role": "tool", "tool_call_id": "x", "content": "y" * 4000}]
    out = engine.compress(msgs)  # must not raise: the turn matters more
    assert isinstance(out, list)
    assert engine.last_run["verdicts"] == 0


def test_threshold_and_usage_accounting(hermes_home):
    mod = load_plugin("jev_compact")
    engine = _engine(mod)
    engine.threshold_tokens = 1000
    assert engine.should_compress(999) is False
    assert engine.should_compress(1000) is True
    engine.update_from_response(
        {"prompt_tokens": 1200, "completion_tokens": 30, "context_length": 200000}
    )
    assert engine.last_prompt_tokens == 1200 and engine.last_total_tokens == 1230
    assert engine.should_compress() is True  # falls back to the last usage


def test_light_pass_is_rate_limited_and_needs_some_history(hermes_home):
    mod = load_plugin("jev_compact")
    engine = _engine(mod)
    msgs = _messages()
    assert engine.prune_tool_results_only(msgs) == (msgs, 0)  # fewer than 4 pairs
    pairs_msgs = []
    for i in range(5):
        pairs_msgs += [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": f"c{i}", "function": {"name": "terminal", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": f"c{i}", "content": "ok"},
        ]
    first = engine.prune_tool_results_only(pairs_msgs)
    second = engine.prune_tool_results_only(pairs_msgs)
    assert isinstance(first, tuple)  # a pass actually ran
    assert second[1] == 0 and second[0] is pairs_msgs  # the next call inside 120s is skipped


def test_registration_hands_over_the_engine(fake_ctx, hermes_home):
    mod = load_plugin("jev_compact")
    mod.register(fake_ctx)
    assert len(fake_ctx.engines) == 1
    assert fake_ctx.engines[0].name == "jev_compact"
