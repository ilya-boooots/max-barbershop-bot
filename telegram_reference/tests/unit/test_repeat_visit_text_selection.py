from app.services.repeat_visit import FALLBACK_TEXT, select_repeat_visit_text


def test_select_repeat_visit_text_returns_one_of_templates(monkeypatch):
    settings = {"templates": ["Текст 1", "Текст 2", "Текст 3", "Текст 4", "Текст 5"]}

    def fake_choice(items):
        assert items == [(1, "Текст 1"), (2, "Текст 2"), (3, "Текст 3"), (4, "Текст 4"), (5, "Текст 5")]
        return items[2]

    monkeypatch.setattr("app.services.repeat_visit.random.choice", fake_choice)
    idx, text = select_repeat_visit_text(settings)
    assert idx == 3
    assert text == "Текст 3"


def test_select_repeat_visit_text_ignores_empty_templates(monkeypatch):
    settings = {"templates": ["", "   ", "Текст 3", None, "Текст 5"]}

    def fake_choice(items):
        assert items == [(3, "Текст 3"), (5, "Текст 5")]
        return items[0]

    monkeypatch.setattr("app.services.repeat_visit.random.choice", fake_choice)
    idx, text = select_repeat_visit_text(settings)
    assert idx == 3
    assert text == "Текст 3"


def test_select_repeat_visit_text_single_template_always_used():
    settings = {"templates": ["", "Текст 2", "", "", ""]}
    idx, text = select_repeat_visit_text(settings)
    assert idx == 2
    assert text == "Текст 2"


def test_select_repeat_visit_text_fallback_when_all_empty():
    settings = {"templates": ["", " ", None, "", "\n"]}
    idx, text = select_repeat_visit_text(settings)
    assert idx == 0
    assert text == FALLBACK_TEXT


def test_select_repeat_visit_text_not_hardcoded_to_first(monkeypatch):
    settings = {"templates": ["Текст 1", "Текст 2"]}

    monkeypatch.setattr("app.services.repeat_visit.random.choice", lambda items: items[-1])
    idx, text = select_repeat_visit_text(settings)

    assert idx == 2
    assert text == "Текст 2"
