from tests.sync import run
from app.keyboards.factory import get_main_menu_kb


def _texts(kb):
    return {b.text for row in kb.keyboard for b in row}


def test_developer_always_gets_developer_menu():
    texts = _texts(run(get_main_menu_kb(378881880, None)))
    assert any("Диагностика" in t for t in texts)


def test_regular_user_has_no_admin_items():
    texts = _texts(run(get_main_menu_kb(111111, "user")))
    assert all("Персонал" not in t and "Рассылка" not in t for t in texts)


def test_admin_sees_admin_items():
    texts = _texts(run(get_main_menu_kb(222222, "admin")))
    assert any("Персонал" in t for t in texts)


def test_manager_sees_manager_items():
    texts = _texts(run(get_main_menu_kb(333333, "manager")))
    assert any("Рассылка" in t for t in texts)
