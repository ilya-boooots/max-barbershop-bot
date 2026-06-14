"""SQLite repository for post-visit feedback requests and responses."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime

PLATFORM_MAX = "max"

@dataclass(frozen=True)
class FeedbackRequest:
    id: int
    platform: str
    platform_user_id: str
    yclients_record_id: str
    yclients_client_id: str | None
    status: str
    requested_at: str | None
    completed_at: str | None
    skipped_at: str | None
    error: str | None

@dataclass(frozen=True)
class FeedbackResponse:
    id: int
    platform: str
    platform_user_id: str
    yclients_record_id: str
    rating: int
    comment: str | None
    is_negative: bool
    admin_notified_at: str | None

class FeedbackRepository:
    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def create_request_if_missing(self, *, platform_user_id: str, yclients_record_id: str, yclients_client_id: str | None = None, platform: str = PLATFORM_MAX) -> FeedbackRequest | None:
        now = _now()
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO feedback_requests (
                    platform, platform_user_id, yclients_record_id, yclients_client_id,
                    status, requested_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'sent', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (platform, platform_user_id, yclients_record_id, yclients_client_id, now),
            )
            connection.commit()
            if cursor.rowcount < 1:
                return None
            return self.get_request(platform_user_id=platform_user_id, yclients_record_id=yclients_record_id, platform=platform)

    def get_request(self, *, platform_user_id: str, yclients_record_id: str, platform: str = PLATFORM_MAX) -> FeedbackRequest | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM feedback_requests WHERE platform=? AND platform_user_id=? AND yclients_record_id=? LIMIT 1",
                (platform, platform_user_id, yclients_record_id),
            ).fetchone()
        return _request(row)

    def find_latest_waiting(self, *, platform_user_id: str, platform: str = PLATFORM_MAX) -> FeedbackRequest | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM feedback_requests
                WHERE platform=? AND platform_user_id=? AND status IN ('sent','waiting_negative_comment')
                ORDER BY requested_at DESC, id DESC LIMIT 1
                """,
                (platform, platform_user_id),
            ).fetchone()
        return _request(row)

    def has_response(self, *, platform_user_id: str, yclients_record_id: str, platform: str = PLATFORM_MAX) -> bool:
        return self.get_response(platform_user_id=platform_user_id, yclients_record_id=yclients_record_id, platform=platform) is not None

    def get_response(self, *, platform_user_id: str, yclients_record_id: str, platform: str = PLATFORM_MAX) -> FeedbackResponse | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM feedback_responses WHERE platform=? AND platform_user_id=? AND yclients_record_id=? LIMIT 1",
                (platform, platform_user_id, yclients_record_id),
            ).fetchone()
        return _response(row)

    def save_rating_once(self, *, platform_user_id: str, yclients_record_id: str, rating: int, is_negative: bool, platform: str = PLATFORM_MAX) -> FeedbackResponse | None:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO feedback_responses (
                    platform, platform_user_id, yclients_record_id, rating, is_negative, created_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (platform, platform_user_id, yclients_record_id, int(rating), int(is_negative)),
            )
            if cursor.rowcount:
                status = "waiting_negative_comment" if is_negative else "rated_positive"
                connection.execute(
                    "UPDATE feedback_requests SET status=?, completed_at=CASE WHEN ?=0 THEN ? ELSE completed_at END, updated_at=CURRENT_TIMESTAMP WHERE platform=? AND platform_user_id=? AND yclients_record_id=?",
                    (status, int(is_negative), _now(), platform, platform_user_id, yclients_record_id),
                )
            connection.commit()
        return self.get_response(platform_user_id=platform_user_id, yclients_record_id=yclients_record_id, platform=platform)

    def save_comment(self, *, platform_user_id: str, yclients_record_id: str, comment: str, platform: str = PLATFORM_MAX) -> FeedbackResponse | None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE feedback_responses SET comment=? WHERE platform=? AND platform_user_id=? AND yclients_record_id=? AND comment IS NULL",
                (comment, platform, platform_user_id, yclients_record_id),
            )
            connection.execute(
                "UPDATE feedback_requests SET status='negative_comment_received', completed_at=?, updated_at=CURRENT_TIMESTAMP WHERE platform=? AND platform_user_id=? AND yclients_record_id=?",
                (_now(), platform, platform_user_id, yclients_record_id),
            )
            connection.commit()
        return self.get_response(platform_user_id=platform_user_id, yclients_record_id=yclients_record_id, platform=platform)

    def mark_admin_notified(self, *, platform_user_id: str, yclients_record_id: str, platform: str = PLATFORM_MAX) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE feedback_responses SET admin_notified_at=COALESCE(admin_notified_at, ?) WHERE platform=? AND platform_user_id=? AND yclients_record_id=?",
                (_now(), platform, platform_user_id, yclients_record_id),
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._database_path)
        con.row_factory = sqlite3.Row
        return con

def _now() -> str:
    return datetime.now(UTC).isoformat()

def _request(row: sqlite3.Row | None) -> FeedbackRequest | None:
    if row is None: return None
    return FeedbackRequest(int(row['id']), row['platform'], row['platform_user_id'], row['yclients_record_id'], row['yclients_client_id'], row['status'], row['requested_at'], row['completed_at'], row['skipped_at'], row['error'])

def _response(row: sqlite3.Row | None) -> FeedbackResponse | None:
    if row is None: return None
    return FeedbackResponse(int(row['id']), row['platform'], row['platform_user_id'], row['yclients_record_id'], int(row['rating']), row['comment'], bool(row['is_negative']), row['admin_notified_at'])
