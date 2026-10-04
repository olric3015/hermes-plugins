"""The plugin's two entry points, with the profile-scoped data directory pointed at tmp_path."""

import time

import pytest


class _Ctx:
    def __init__(self):
        self.hooks, self.commands = {}, {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler


@pytest.fixture
def wired(plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "_ledger_path", lambda: tmp_path / plugin.ledger.FILENAME)
    ctx = _Ctx()
    plugin.register(ctx)
    return ctx


def _call(hook, **over):
    payload = {"aux_task": "compression", "session_id": "s1", "provider": "custom", "model": "m",
               "ended_at": time.time(), "api_duration": 0.5,
               "usage": {"input_tokens": 300, "output_tokens": 40}}
    payload.update(over)
    hook(**payload)


def test_registers_exactly_the_declared_surface(wired):
    assert sorted(wired.hooks) == ["post_auxiliary_call"]
    assert sorted(wired.commands) == ["aux"]


def test_hook_records_and_command_reports(wired):
    hook, aux = wired.hooks["post_auxiliary_call"], wired.commands["aux"]
    assert aux("") == "Auxiliary LLM calls, last 24 hours: none recorded."
    _call(hook)
    _call(hook, aux_task="title_generation", session_id="s2", usage={"input_tokens": 20, "output_tokens": 5})
    day = aux("").splitlines()
    assert day[3].split()[:5] == ["compression", "1", "0", "300", "40"]
    assert day[5].split()[:5] == ["total", "2", "0", "320", "45"]
    session = aux("session").splitlines()
    assert session[0] == "Auxiliary LLM calls, latest session:"
    assert session[3].split()[:2] == ["title_generation", "1"]
    assert aux("all models").splitlines()[3].split()[:2] == ["m", "2"]


def test_command_help_unknown_and_clear(wired):
    hook, aux = wired.hooks["post_auxiliary_call"], wired.commands["aux"]
    assert aux("help").startswith("/aux:")
    assert aux("bogus").startswith("Unknown option: bogus")
    _call(hook)
    assert aux("clear") == "aux-ledger: deleted 1 recorded call(s)."
    assert aux("all") == "Auxiliary LLM calls, all recorded: none recorded."


def test_hook_never_raises(plugin, monkeypatch):
    def boom():
        raise OSError("read-only home")

    monkeypatch.setattr(plugin, "_ledger_path", boom)
    plugin._on_post_auxiliary_call(aux_task="vision")
