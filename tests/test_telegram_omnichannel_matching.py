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


def max_user(phone=None, yclients_client_id=None):
    return User(1, PLATFORM_MAX, "max1", "max1", "chatm", None, None, None, None, phone, None, "user", yclients_client_id, True)


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
