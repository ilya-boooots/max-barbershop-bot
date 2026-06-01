from __future__ import annotations

from pathlib import Path
import re

from app.ui.callbacks import NAV_HOME, NAV_BACK


def _iter_python_files() -> list[Path]:
    return [p for p in Path("app").rglob("*.py") if p.is_file()]


def test_all_home_buttons_use_global_callback() -> None:
    pattern = re.compile(r'text\s*=\s*["\']🏠 Главное меню["\'].*callback_data\s*=\s*([^\]\),]+)')
    bad: list[str] = []
    for path in _iter_python_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "🏠 Главное меню" not in line or "callback_data" not in line:
                continue
            if "nav:home" in line or "NAV_HOME" in line or "NAV_HOME_CALLBACK" in line:
                continue
            bad.append(f"{path}:{lineno}:{line.strip()}")
    assert not bad, "Найдены home-кнопки не на глобальном callback:\n" + "\n".join(bad)


def test_contacts_back_not_legacy_maps() -> None:
    text = Path("app/keyboards/menu.py").read_text(encoding="utf-8")
    assert 'text=BACK_BTN, callback_data=NAV_BACK_CALLBACK' in text
    assert 'callback_data="contacts:maps"' not in text


def test_nav_callback_symbols_are_stable() -> None:
    assert NAV_HOME == "nav:home"
    assert NAV_BACK == "nav:back"
    assert len(NAV_HOME.encode("utf-8")) <= 64
    assert len(NAV_BACK.encode("utf-8")) <= 64
