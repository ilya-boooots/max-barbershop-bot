"""SQLite repository for post-visit feedback requests and responses."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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
    status: str = "open"
    closed_by_platform_user_id: str | None = None
    closed_at: str | None = None

@dataclass(frozen=True)
class FeedbackAdminReply:
    id: int
    feedback_response_id: int
    created_at: str
    admin_platform_user_id: str
    text: str

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

    def get_response_by_id(self, response_id: int) -> FeedbackResponse | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM feedback_responses WHERE id=? LIMIT 1",
                (int(response_id),),
            ).fetchone()
        return _response(row)

    def save_rating_once(self, *, platform_user_id: str, yclients_record_id: str, rating: int, is_negative: bool, platform: str = PLATFORM_MAX) -> FeedbackResponse | None:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO feedback_responses (
                    platform, platform_user_id, yclients_record_id, rating, is_negative, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'open', CURRENT_TIMESTAMP)
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

    def close_response(self, *, response_id: int, admin_platform_user_id: str) -> FeedbackResponse | None:
        now = _now()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE feedback_responses
                SET status='closed', closed_by_platform_user_id=?, closed_at=?
                WHERE id=?
                """,
                (_required_text(admin_platform_user_id, "admin_platform_user_id"), now, int(response_id)),
            )
            connection.commit()
        return self.get_response_by_id(response_id)

    def save_admin_reply(self, *, response_id: int, admin_platform_user_id: str, text: str) -> FeedbackAdminReply | None:
        now = _now()
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO feedback_admin_replies (
                    feedback_response_id, created_at, admin_platform_user_id, text
                ) VALUES (?, ?, ?, ?)
                """,
                (int(response_id), now, _required_text(admin_platform_user_id, "admin_platform_user_id"), _required_text(text, "text")),
            )
            connection.execute(
                "UPDATE feedback_responses SET status='admin_replied' WHERE id=?",
                (int(response_id),),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM feedback_admin_replies WHERE id=?", (cursor.lastrowid,)).fetchone()
        return _reply(row)

    def get_response_context(self, response_id: int) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    r.id AS response_id, r.platform, r.platform_user_id, r.yclients_record_id,
                    r.rating, r.comment, r.is_negative, r.admin_notified_at, r.status,
                    u.display_name, u.first_name, u.last_name, u.username, u.phone, u.max_user_id, u.chat_id,
                    req.yclients_client_id, req.requested_at, req.completed_at,
                    nh.scheduled_for
                FROM feedback_responses r
                LEFT JOIN users u ON u.platform = r.platform AND u.platform_user_id = r.platform_user_id
                LEFT JOIN feedback_requests req ON req.platform = r.platform
                    AND req.platform_user_id = r.platform_user_id
                    AND req.yclients_record_id = r.yclients_record_id
                LEFT JOIN notification_history nh ON nh.platform = r.platform
                    AND nh.platform_user_id = r.platform_user_id
                    AND nh.yclients_record_id = r.yclients_record_id
                    AND nh.notification_type = 'post_visit_feedback_request'
                WHERE r.id=?
                ORDER BY nh.id DESC
                LIMIT 1
                """,
                (int(response_id),),
            ).fetchone()
        return dict(row) if row else {}

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
    keys = set(row.keys())
    return FeedbackResponse(
        int(row['id']),
        row['platform'],
        row['platform_user_id'],
        row['yclients_record_id'],
        int(row['rating']),
        row['comment'],
        bool(row['is_negative']),
        row['admin_notified_at'],
        row['status'] if 'status' in keys else 'open',
        row['closed_by_platform_user_id'] if 'closed_by_platform_user_id' in keys else None,
        row['closed_at'] if 'closed_at' in keys else None,
    )

def _reply(row: sqlite3.Row | None) -> FeedbackAdminReply | None:
    if row is None: return None
    return FeedbackAdminReply(int(row['id']), int(row['feedback_response_id']), row['created_at'], row['admin_platform_user_id'], row['text'])

def _required_text(value: str | None, field_name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text
