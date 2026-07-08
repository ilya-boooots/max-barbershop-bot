import asyncio

from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.core import state
from max_barbershop_bot.flows import client_segments
from max_barbershop_bot.repositories.yclients_settings import YClientsSettings
from max_barbershop_bot.services import client_segments as segments
from max_barbershop_bot.ui.buttons import SEGMENTS_BROADCAST_PAYLOAD, SEGMENTS_BY_MASTER_PAYLOAD, SEGMENTS_BY_MASTER_PREFIX


class FakeSender:
    def __init__(self):
        self.messages = []
        self.callbacks = []

    async def send_to_chat(self, chat_id, text, keyboard=None, attachments=None):
        self.messages.append((text, keyboard))

    async def send_to_user(self, user_id, text, keyboard=None, attachments=None):
        self.messages.append((text, keyboard))

    async def answer_callback(self, callback_id):
        self.callbacks.append(callback_id)


def make_context(payload="segments:by_master", user="u-by-master"):
    sender = FakeSender()
    event = NormalizedEvent(
        update_type="message_callback",
        platform_user_id=user,
        max_user_id=user,
        chat_id="100500",
        text=None,
        callback_payload=payload,
        callback_id="cb-1",
    )
    return RouterContext(event=event, sender=sender), sender


def button_texts(keyboard):
    return [button.text for row in keyboard.rows for button in row]


def button_payloads(keyboard):
    return [button.payload for row in keyboard.rows for button in row]


def test_by_master_picker_callback_opens_picker_uses_source_and_payloads(monkeypatch):
    calls = {"list_masters": 0}

    class FakeSegmentService:
        def __init__(self, *args, **kwargs):
            pass

        async def list_masters(self):
            calls["list_masters"] += 1
            return [{"id": "42", "title": "Артур"}]

    monkeypatch.setattr(client_segments, "_can_open_segments", lambda context: True)
    monkeypatch.setattr(client_segments, "ClientSegmentService", FakeSegmentService)
    context, sender = make_context(SEGMENTS_BY_MASTER_PAYLOAD)

    asyncio.run(client_segments.handle_segment_selected(context))

    assert calls["list_masters"] == 1
    assert sender.messages[-1][0] == "💈 Выберите мастера"
    assert "Артур" in button_texts(sender.messages[-1][1])
    assert f"{SEGMENTS_BY_MASTER_PREFIX}42" in button_payloads(sender.messages[-1][1])


def test_by_master_picker_empty_list_and_load_error_are_friendly(monkeypatch):
    class EmptySegmentService:
        def __init__(self, *args, **kwargs):
            pass

        async def list_masters(self):
            return []

    monkeypatch.setattr(client_segments, "_can_open_segments", lambda context: True)
    monkeypatch.setattr(client_segments, "ClientSegmentService", EmptySegmentService)
    context, sender = make_context(SEGMENTS_BY_MASTER_PAYLOAD, user="empty-master")
    asyncio.run(client_segments.handle_segment_selected(context))
    assert sender.messages[-1][0] == client_segments.MASTER_PICKER_LOAD_ERROR_TEXT

    class BrokenSegmentService(EmptySegmentService):
        async def list_masters(self):
            raise segments.ClientSegmentsLoadError("boom")

    monkeypatch.setattr(client_segments, "ClientSegmentService", BrokenSegmentService)
    context, sender = make_context(SEGMENTS_BY_MASTER_PAYLOAD, user="broken-master")
    asyncio.run(client_segments.handle_segment_selected(context))
    assert sender.messages[-1][0] == client_segments.MASTER_PICKER_LOAD_ERROR_TEXT


def test_by_master_invalid_stale_callback_is_friendly(monkeypatch):
    monkeypatch.setattr(client_segments, "_can_open_segments", lambda context: True)
    for payload in (SEGMENTS_BY_MASTER_PREFIX, f"{SEGMENTS_BY_MASTER_PREFIX}picker"):
        context, sender = make_context(payload, user=f"stale-{payload[-1:]}")
        asyncio.run(client_segments.handle_segment_selected(context))
        assert sender.messages[-1][0] == client_segments.SEGMENT_STALE_TEXT


def test_by_master_detail_uses_selected_resolver_title_count_empty_and_buttons(monkeypatch):
    calls = []

    class FakeSegmentService:
        def __init__(self, *args, **kwargs):
            pass

        async def get_clients_by_master(self, master_id):
            calls.append(master_id)
            return segments.ClientSegmentResult(
                segment_type="by_master",
                title="💈 Клиенты мастера: Артур",
                description="Выбор клиентов по мастеру из истории YClients.",
                members=[],
                branch_timezone="UTC",
                calculated_at="2026-07-07T12:00:00+00:00",
                diagnostics={"master_id": master_id},
            )

    monkeypatch.setattr(client_segments, "_can_open_segments", lambda context: True)
    monkeypatch.setattr(client_segments, "ClientSegmentService", FakeSegmentService)
    monkeypatch.setattr(client_segments, "_map_members_to_recipients", lambda members: [])
    context, sender = make_context(f"{SEGMENTS_BY_MASTER_PREFIX}42", user="detail-master")
    asyncio.run(client_segments.handle_segment_selected(context))

    assert calls == ["42"]
    text, keyboard = sender.messages[-1]
    assert "💈 Клиенты мастера: Артур" in text
    assert "Количество клиентов: 0" in text
    assert "Обновлено: 07.07.2026 12:00" in text
    assert "😌 В этом сегменте пока нет клиентов." in text
    assert button_texts(keyboard) == ["📣 Использовать для рассылки", "🔄 Обновить", "⬅️ Назад", "🏠 Главное меню"]
    assert f"{SEGMENTS_BY_MASTER_PREFIX}42" in button_payloads(keyboard)


def test_by_master_service_count_dedupes_unique_clients_and_uses_master_name(monkeypatch):
    now = "2026-07-07T12:00:00+00:00"
    records = [
        {"id": 1, "datetime": now, "staff_id": "42", "client": {"id": "101", "phone": "+79990000001", "name": "Анна"}},
        {"id": 2, "datetime": now, "staff": {"id": "42"}, "client": {"id": "101", "phone": "+79990000001", "name": "Анна"}},
        {"id": 3, "datetime": now, "staff_id": "7", "client": {"id": "102", "phone": "+79990000002", "name": "Иван"}},
    ]
    service = segments.ClientSegmentService(settings_repository=None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        service,
        "_require_settings",
        lambda: YClientsSettings(company_id="1", partner_token="partner", user_token="user", branch_timezone="UTC"),
    )

    async def fake_fetch_records(settings, *, date_from, date_to):
        return records

    monkeypatch.setattr(service, "_fetch_records", fake_fetch_records)
    async def fake_list_masters():
        return [{"id": "42", "title": "Артур"}]

    monkeypatch.setattr(service, "list_masters", fake_list_masters)

    result = asyncio.run(service.get_clients_by_master("42"))

    assert result.title == "💈 Клиенты мастера: Артур"
    assert result.count == 1
    assert result.members[0].yclients_client_id == "101"


def test_by_master_broadcast_handoff_preserves_id_and_opens_text_flow_only(monkeypatch):
    opened = []
    context, sender = make_context(SEGMENTS_BROADCAST_PAYLOAD, user="broadcast-master")
    state.set_state_data_value("broadcast-master", "100500", client_segments._SELECTED_SEGMENT_PAYLOAD_KEY, f"{SEGMENTS_BY_MASTER_PREFIX}42")
    state.set_state_data_value(
        "broadcast-master",
        "100500",
        client_segments._SELECTED_SEGMENT_RESULT_KEY,
        segments.ClientSegmentResult(segment_type="by_master", title="💈 Клиенты мастера: Артур", members=[]),
    )
    state.set_state_data_value("broadcast-master", "100500", client_segments._SELECTED_SEGMENT_RECIPIENTS_KEY, [])

    async def fake_open_segment_broadcast_text(context, *, audience_key, audience_label, recipients, return_screen=state.CLIENT_SEGMENT_RESULT_SCREEN):
        opened.append((audience_key, audience_label, recipients, return_screen))

    monkeypatch.setattr(client_segments, "_can_open_segments", lambda context: True)
    monkeypatch.setattr(client_segments, "open_segment_broadcast_text", fake_open_segment_broadcast_text)

    asyncio.run(client_segments.handle_segment_broadcast(context))

    assert opened == [("by_master:42", "💈 Клиенты мастера: Артур · YClients: 0", [], state.CLIENT_SEGMENT_RESULT_SCREEN)]
    assert not sender.messages


def test_by_master_pr_does_not_touch_staff_settings_or_other_segment_logic():
    source = client_segments.__dict__["handle_segment_broadcast"].__code__.co_names
    assert "open_segment_broadcast_text" in source
    assert client_segments._audience_key_from_segment_payload(f"{SEGMENTS_BY_MASTER_PREFIX}42", "by_master") == "by_master:42"
    assert client_segments._audience_key_from_segment_payload("segments:active_30", "active_30") == "active_30"
    assert client_segments._audience_key_from_segment_payload("segments:lost_30", "lost_30") == "lost_30"
    assert client_segments._audience_key_from_segment_payload("segments:no_future", "no_future_bookings") == "no_future_bookings"
    assert client_segments._audience_key_from_segment_payload("segments:cancelled", "cancelled") == "cancelled_recent"
