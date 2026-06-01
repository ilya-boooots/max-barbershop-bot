from app.db.sqlite import init_db, fetchone
from tests.sync import run


def test_init_db_is_idempotent(initialized_db):
    run(init_db())
    row = run(fetchone("SELECT name FROM sqlite_master WHERE type='table' AND name='users'"))
    assert row is not None
