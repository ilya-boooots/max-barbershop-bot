from __future__ import annotations

import pytest

from app.core.navigation import NAV_STACK_KEY, home_handler, peek_screen, push_screen, render_previous_screen
from tests.helpers import FakeCallback, FakeMessage
from tests.sync import run


pytestmark = pytest.mark.unit


def test_push_pop_back_happy_path(fsm_context, monkeypatch: pytest.MonkeyPatch):
    run(push_screen(fsm_context, "main_menu"))
    run(push_screen(fsm_context, "screen_a", {"x": 1}))
    run(push_screen(fsm_context, "screen_b", {"x": 2}))

    rendered: list[str] = []

    async def fake_render_screen(screen_id, *_args, **_kwargs):
        rendered.append(screen_id)

    monkeypatch.setattr("app.core.navigation.render_screen", fake_render_screen)
    msg = FakeMessage(user_id=2001)
    run(render_previous_screen(msg, fsm_context))

    assert rendered == ["screen_a"]
    prev = run(peek_screen(fsm_context))
    assert prev == ("screen_a", {"x": 1})


def test_home_clears_stack(fsm_context, monkeypatch: pytest.MonkeyPatch):
    run(push_screen(fsm_context, "screen_a"))
    callback = FakeCallback(user_id=2001)

    called = {"main": 0}

    async def fake_render_main(*_args, **_kwargs):
        called["main"] += 1

    monkeypatch.setattr("app.core.navigation.render_main_menu_for_user", fake_render_main)
    run(home_handler(callback, fsm_context))

    data = run(fsm_context.get_data())
    assert data[NAV_STACK_KEY] == []
    assert called["main"] == 1


def test_corrupted_history_falls_back_to_main_menu(fsm_context, monkeypatch: pytest.MonkeyPatch):
    run(fsm_context.update_data({NAV_STACK_KEY: "broken"}))
    msg = FakeMessage(user_id=2001)
    called = {"main": 0}

    async def fake_render_main(*_args, **_kwargs):
        called["main"] += 1

    monkeypatch.setattr("app.core.navigation.render_main_menu_for_user", fake_render_main)
    run(render_previous_screen(msg, fsm_context))

    assert called["main"] == 1
