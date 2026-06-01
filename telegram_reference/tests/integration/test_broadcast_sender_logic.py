from tests.sync import run
from app.repositories import broadcasts as broadcasts_repo


def test_send_to_self_audience_has_actor():
    rows = run(broadcasts_repo.resolve_one_time_audience("send_to_self", actor_id=123))
    assert len(rows) == 1 and int(rows[0].get("tg_id") or rows[0].get("user_id")) == 123
