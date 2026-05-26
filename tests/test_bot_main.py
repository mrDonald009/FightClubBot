"""Тесты оркестрации запуска из bot.main()."""

from types import SimpleNamespace

import pytest

import bot

pytestmark = pytest.mark.flow


def test_main_happy_path_calls_startup_setup_and_polling(monkeypatch):
    calls = []

    class _FakeRegistrar:
        pass

    fake_app = SimpleNamespace(run_polling=lambda: calls.append("polling"))
    fake_config = object()

    monkeypatch.setattr(bot, "Config", lambda: fake_config)
    monkeypatch.setattr(bot, "initialize_app", lambda cfg: calls.append(("init", cfg)))
    monkeypatch.setattr(bot.ApplicationFactory, "create", lambda cfg: fake_app)
    monkeypatch.setattr(bot, "HandlerRegistrar", lambda: _FakeRegistrar())
    monkeypatch.setattr(bot, "register_all_handlers", lambda reg: calls.append(("register", type(reg).__name__)))
    monkeypatch.setattr(
        bot.ApplicationFactory,
        "setup_application",
        lambda app, reg: calls.append(("setup", app is fake_app, isinstance(reg, _FakeRegistrar))),
    )
    monkeypatch.setattr(bot, "setup_scheduled_jobs", lambda app, cfg: calls.append(("jobs", app is fake_app, cfg is fake_config)))

    bot.main()

    assert ("init", fake_config) in calls
    assert ("register", "_FakeRegistrar") in calls
    assert ("setup", True, True) in calls
    assert ("jobs", True, True) in calls
    assert "polling" in calls


def test_main_keyboard_interrupt_is_handled(monkeypatch):
    fake_app = SimpleNamespace(run_polling=lambda: (_ for _ in ()).throw(KeyboardInterrupt()))

    monkeypatch.setattr(bot, "Config", lambda: object())
    monkeypatch.setattr(bot, "initialize_app", lambda _cfg: None)
    monkeypatch.setattr(bot.ApplicationFactory, "create", lambda _cfg: fake_app)
    monkeypatch.setattr(bot, "HandlerRegistrar", lambda: object())
    monkeypatch.setattr(bot, "register_all_handlers", lambda _reg: None)
    monkeypatch.setattr(bot.ApplicationFactory, "setup_application", lambda _app, _reg: None)
    monkeypatch.setattr(bot, "setup_scheduled_jobs", lambda _app, _cfg: None)
    monkeypatch.setattr(bot.sys, "exit", lambda code=0: (_ for _ in ()).throw(AssertionError(f"exit called: {code}")))

    bot.main()


def test_main_exits_on_unhandled_error(monkeypatch):
    monkeypatch.setattr(bot, "Config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(SystemExit) as exc:
        bot.main()

    assert exc.value.code == 1

