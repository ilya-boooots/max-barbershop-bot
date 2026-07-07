from max_barbershop_bot.integrations.yclients.dto import YClientsNormalizedClient
from max_barbershop_bot.repositories.telegram_users import TelegramUserRecord
from max_barbershop_bot.repositories.users import PLATFORM_MAX, PLATFORM_TELEGRAM, User
from max_barbershop_bot.services.omnichannel_broadcasts import OmnichannelBroadcastService


class FakeTelegramRepo:
    def __init__(self, users):
        self.users = users

    def list_users_for_broadcast_audience(self, *, platform=None):
        return self.users

    def list_by_yclients_client_id(self, yclients_client_id, *, platform=None):
        return [u for u in self.users if u.yclients_client_id == str(yclients_client_id)]

    def list_by_phone_keys(self, keys, *, platform=None):
        return [u for u in self.users if set(u.phone_keys) & keys]

    def find_by_platform_user_id(self, platform_user_id, *, platform=None):
        return next((u for u in self.users if u.platform_user_id == platform_user_id), None)


class FakeUsersRepo:
    def __init__(self, users=()):
        self.users = list(users)

    def list_users_for_broadcast_audience(self, *, platform=None):
        return [u for u in self.users if platform is None or u.platform == platform]

    def list_by_yclients_client_id(self, yclients_client_id, *, platform=None):
        return [u for u in self.users if u.yclients_client_id == str(yclients_client_id) and (platform is None or u.platform == platform)]

    def list_by_phone_keys(self, keys, *, platform=None):
        from max_barbershop_bot.services.phone_normalization import build_phone_match_keys
        return [u for u in self.users if (platform is None or u.platform == platform) and build_phone_match_keys(u.phone) & keys]

    def find_by_platform_user_id(self, platform_user_id, *, platform=None):
        return next((u for u in self.users if u.platform_user_id == platform_user_id and (platform is None or u.platform == platform)), None)


class FakeAttributionRepo:
    def list_by_booking_phone_keys(self, keys, *, platform=None):
        return []

    def list_by_yclients_client_id(self, yclients_client_id):
        return []


class FakeHistoryRepo:
    pass


def tg_user(**kwargs):
    from max_barbershop_bot.services.phone_normalization import build_phone_match_keys
    phone = kwargs.get("phone")
    return TelegramUserRecord(
        id=1,
        platform=PLATFORM_TELEGRAM,
        platform_user_id=kwargs.get("platform_user_id", "tg1"),
        chat_id=kwargs.get("chat_id", "chat1"),
        phone=phone,
        phone_keys=frozenset(build_phone_match_keys(phone)),
        yclients_client_id=kwargs.get("yclients_client_id"),
        notifications_enabled=kwargs.get("notifications_enabled", True),
        blocked=kwargs.get("blocked", False),
        stopped=kwargs.get("stopped", False),
    )


def max_user(phone=None, yclients_client_id=None, notifications_enabled=True):
    return User(1, PLATFORM_MAX, "max1", "max1", "chatm", None, None, None, None, phone, None, "user", yclients_client_id, notifications_enabled)


def service(tg_users, max_users=()):
    return OmnichannelBroadcastService(
        users_repository=FakeUsersRepo(max_users),
        telegram_users_repository=FakeTelegramRepo(tg_users),
        attribution_repository=FakeAttributionRepo(),
        history_repository=FakeHistoryRepo(),
        adapters={},
    )


def target_for(svc, client):
    return svc.resolve_delivery_target_for_yclients_client(client)


def test_telegram_phone_8_matches_plus7():
    target = target_for(service([tg_user(phone="+79198332692")]), YClientsNormalizedClient(id="1", phones=("89198332692",)))
    assert target.platform == PLATFORM_TELEGRAM
    assert target.reason == "telegram_selected"


def test_telegram_last10_matches_plus7():
    target = target_for(service([tg_user(phone="9198332692")]), YClientsNormalizedClient(id="1", phones=("+79198332692",)))
    assert target.platform == PLATFORM_TELEGRAM


def test_telegram_client_id_match():
    target = target_for(service([tg_user(yclients_client_id="123")]), YClientsNormalizedClient(id="123"))
    assert target.platform == PLATFORM_TELEGRAM


def test_telegram_wins_when_max_also_matches():
    target = target_for(service([tg_user(phone="+79198332692")], [max_user(phone="89198332692")]), YClientsNormalizedClient(id="1", phones=("89198332692",)))
    assert target.platform == PLATFORM_TELEGRAM


def test_telegram_chat_without_identity_not_matched():
    svc = service([tg_user(phone=None)])
    target = target_for(svc, YClientsNormalizedClient(id="1", phones=("89198332692",)))
    assert target.platform is None
    assert svc.telegram_matching_diagnostics([YClientsNormalizedClient(id="1", phones=("89198332692",))])["telegram_unmatched_reason"] == "Telegram users found, but they have no phone/yclients_client_id for matching"


def test_telegram_null_notifications_deliverable_by_default():
    user = tg_user(phone="+79198332692", notifications_enabled=True)
    target = target_for(service([user]), YClientsNormalizedClient(id="1", phones=("89198332692",)))
    assert target.platform == PLATFORM_TELEGRAM


def test_telegram_blocked_skipped():
    target = target_for(service([tg_user(phone="+79198332692", blocked=True)]), YClientsNormalizedClient(id="1", phones=("89198332692",)))
    assert target.platform is None


def test_required_case_phone_match_counters_select_telegram_not_max():
    svc = service([tg_user(chat_id="111", phone="+79198332692")], [max_user(phone="89198332692")])
    estimate = svc.estimate([YClientsNormalizedClient(id="1", phones=("89198332692",))])
    assert estimate.telegram_selected == 1
    assert estimate.max_selected == 0
    assert estimate.telegram_matching_diagnostics["telegram_matched_by_phone_count"] == 1


def test_required_case_client_id_match_counters():
    svc = service([tg_user(chat_id="111", yclients_client_id="123")])
    estimate = svc.estimate([YClientsNormalizedClient(id="123")])
    assert estimate.telegram_selected == 1
    assert estimate.telegram_matching_diagnostics["telegram_matched_by_client_id_count"] == 1


def test_required_case_telegram_and_max_duplicate_priority_count():
    svc = service([tg_user(chat_id="111", yclients_client_id="123")], [max_user(yclients_client_id="123")])
    estimate = svc.estimate([YClientsNormalizedClient(id="123")])
    assert estimate.telegram_selected == 1
    assert estimate.max_selected == 0
    assert estimate.duplicates_excluded == 1
    assert estimate.telegram_matching_diagnostics["telegram_priority_duplicate_skipped_count"] == 1


def test_required_case_null_notifications_deliverable():
    svc = service([tg_user(chat_id="111", phone="+79198332692", notifications_enabled=None)])
    estimate = svc.estimate([YClientsNormalizedClient(id="1", phones=("89198332692",))])
    assert estimate.telegram_selected == 1
    assert estimate.telegram_matching_diagnostics["telegram_matches_rejected_not_deliverable_count"] == 0


def test_required_case_blocked_falls_back_to_max_and_counts_rejection():
    svc = service([tg_user(chat_id="111", phone="+79198332692", blocked=True)], [max_user(phone="89198332692")])
    estimate = svc.estimate([YClientsNormalizedClient(id="1", phones=("89198332692",))])
    assert estimate.telegram_selected == 0
    assert estimate.max_selected == 1
    assert estimate.telegram_matching_diagnostics["rejected_blocked_count"] == 1


def test_manual_broadcast_selects_telegram_when_notifications_disabled():
    svc = service([tg_user(chat_id="111", phone="+79198332692", notifications_enabled=False)])
    estimate = svc.estimate([YClientsNormalizedClient(id="1", phones=("89198332692",))])
    assert estimate.telegram_selected == 1
    assert estimate.telegram_matching_diagnostics["telegram_matches_rejected_not_deliverable_count"] == 0
    assert estimate.telegram_matching_diagnostics["rejected_notifications_disabled_count"] == 0


def test_manual_broadcast_skips_blocked_even_when_notifications_disabled():
    svc = service([tg_user(chat_id="111", phone="+79198332692", notifications_enabled=False, blocked=True)])
    estimate = svc.estimate([YClientsNormalizedClient(id="1", phones=("89198332692",))])
    assert estimate.telegram_selected == 0
    assert estimate.telegram_matching_diagnostics["rejected_blocked_count"] == 1


def test_reminder_deliverability_still_respects_notifications_disabled():
    svc = service([tg_user(chat_id="111", phone="+79198332692", notifications_enabled=False)])
    user = tg_user(chat_id="111", phone="+79198332692", notifications_enabled=False)
    assert svc._deliverability_for_reminder(user, PLATFORM_TELEGRAM).deliverable is False


def test_manual_broadcast_telegram_priority_ignores_notifications_disabled():
    svc = service(
        [tg_user(chat_id="111", phone="+79198332692", notifications_enabled=False)],
        [max_user(phone="89198332692", notifications_enabled=True)],
    )
    estimate = svc.estimate([YClientsNormalizedClient(id="1", phones=("89198332692",))])
    assert estimate.telegram_selected == 1
    assert estimate.max_selected == 0
    assert estimate.duplicates_excluded == 1


def test_required_case_intersection_selects_unless_rejected():
    svc = service([tg_user(chat_id="111", phone="+79198332692")])
    diagnostics = svc.telegram_matching_diagnostics([YClientsNormalizedClient(id="1", phones=("89198332692",))])
    assert diagnostics["phone_key_intersection_count"] > 0
    assert diagnostics["telegram_matched_by_phone_count"] > 0
    assert diagnostics["telegram_matching_resolver_invariant_failed"] is False


def test_all_audience_estimate_distinguishes_total_yclients_from_eligible_max():
    svc = service([], [max_user(yclients_client_id="1")])
    estimate = svc.estimate([
        YClientsNormalizedClient(id="1", phones=("+79990000001",)),
        YClientsNormalizedClient(id="2", phones=("+79990000002",)),
    ])

    assert estimate.total_yclients_clients == 2
    assert estimate.max_selected == 1
    assert estimate.unreachable == 1
    assert estimate.total_deliveries == 1


def test_unmapped_yclients_client_is_missing_not_sent():
    svc = service([], [])
    target = svc.resolve_delivery_target_for_yclients_client(YClientsNormalizedClient(id="missing", phones=("+79990000003",)))

    assert target.platform is None
    assert target.reason == "skipped_unreachable"


def test_duplicate_yclients_clients_mapped_to_same_max_user_are_deduped():
    svc = service([], [max_user(phone="+79990000001")])
    estimate = svc.estimate([
        YClientsNormalizedClient(id="1", phones=("+79990000001",)),
        YClientsNormalizedClient(id="2", phones=("89990000001",)),
    ])

    assert estimate.max_selected == 1
    assert estimate.duplicates_excluded == 1
    assert estimate.telegram_matching_diagnostics["duplicate_recipient_skipped_count"] == 1


def test_manual_all_audience_disabled_notifications_match_telegram_and_still_sendable():
    svc = service([], [max_user(phone="+79990000001", notifications_enabled=False)])
    estimate = svc.estimate([YClientsNormalizedClient(id="1", phones=("+79990000001",))])

    assert estimate.max_selected == 1
    assert estimate.telegram_matching_diagnostics["rejected_notifications_disabled_count"] == 0


class FakeOmniHistoryRepo:
    def __init__(self):
        self.broadcasts = []
        self.deliveries = []
        self.statuses = []

    def upsert_broadcast(self, **kwargs):
        self.broadcasts.append(kwargs)

    def mark_status(self, broadcast_id, status, **kwargs):
        self.statuses.append({"broadcast_id": broadcast_id, "status": status, **kwargs})

    def add_delivery(self, **kwargs):
        self.deliveries.append(kwargs)


class FakeAdapter:
    platform = PLATFORM_MAX

    def __init__(self, outcomes=None):
        self.outcomes = outcomes or {}
        self.sent = []

    def can_send(self, target):
        return True

    async def send_text(self, target, text):
        self.sent.append((target.platform_user_id, text))
        return self.outcomes.get(target.platform_user_id, (True, None))

    async def send_media(self, target, text, attachment):
        return await self.send_text(target, text)

    def format_error(self, error):
        return type(error).__name__


def service_with_history(max_users, adapter):
    history = FakeOmniHistoryRepo()
    svc = OmnichannelBroadcastService(
        users_repository=FakeUsersRepo(max_users),
        telegram_users_repository=FakeTelegramRepo([]),
        attribution_repository=FakeAttributionRepo(),
        history_repository=history,
        adapters={PLATFORM_MAX: adapter},
    )
    return svc, history


def test_confirm_send_creates_one_run_and_delivery_per_eligible_or_skipped_outcome():
    import asyncio

    adapter = FakeAdapter(outcomes={"max1": (True, None)})
    svc, history = service_with_history([max_user(yclients_client_id="1")], adapter)

    report = asyncio.run(svc.send(
        clients=[YClientsNormalizedClient(id="1"), YClientsNormalizedClient(id="2")],
        text="Тестовая рассылка",
        origin_platform=PLATFORM_MAX,
        created_by_user_id="owner",
        broadcast_id="bid-test",
        sleep_seconds=0,
    ))

    assert len(history.broadcasts) == 1
    assert len(history.deliveries) == 2
    assert [d["delivery_status"] for d in history.deliveries] == ["sent", "skipped_unreachable"]
    assert report.max_sent == 1
    assert report.skipped_unreachable == 1


def test_send_report_counts_sent_skipped_failed_blocked_and_dedup():
    import asyncio

    adapter = FakeAdapter(outcomes={"max1": (True, None), "max2": (False, "skipped_blocked"), "max3": (False, "boom")})
    users = [
        User(1, PLATFORM_MAX, "max1", "max1", "chat1", None, None, None, None, "+79990000001", None, "user", "1", True),
        User(2, PLATFORM_MAX, "max2", "max2", "chat2", None, None, None, None, "+79990000002", None, "user", "2", True),
        User(3, PLATFORM_MAX, "max3", "max3", "chat3", None, None, None, None, "+79990000003", None, "user", "3", True),
    ]
    svc, history = service_with_history(users, adapter)

    report = asyncio.run(svc.send(
        clients=[
            YClientsNormalizedClient(id="1", phones=("+79990000001",)),
            YClientsNormalizedClient(id="2", phones=("+79990000002",)),
            YClientsNormalizedClient(id="3", phones=("+79990000003",)),
            YClientsNormalizedClient(id="4", phones=("+79990000001",)),
            YClientsNormalizedClient(id="5", phones=("+79990000005",)),
        ],
        text="Отчёт без телефона 79990000001 и token",
        origin_platform=PLATFORM_MAX,
        created_by_user_id="owner",
        broadcast_id="bid-report",
        sleep_seconds=0,
    ))

    assert report.max_sent == 1
    assert report.skipped_blocked == 1
    assert report.failed == 1
    assert report.skipped_duplicate == 1
    assert report.skipped_unreachable == 1
    assert [d["delivery_status"] for d in history.deliveries] == ["sent", "skipped_blocked", "failed", "skipped_duplicate", "skipped_unreachable"]
