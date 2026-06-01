from __future__ import annotations

from typing import Any, Iterable

import aiosqlite
from app.config import get_db_path


async def init_db() -> None:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                phone TEXT NOT NULL,
                name TEXT NOT NULL,
                display_name TEXT,
                birth_date TEXT NOT NULL,
                gender TEXT NOT NULL,
                is_registered INTEGER NOT NULL DEFAULT 0,
                loyalty_balance INTEGER NOT NULL DEFAULT 0,
                bonus_balance INTEGER NOT NULL DEFAULT 0,
                first_purchase_done INTEGER NOT NULL DEFAULT 0,
                role TEXT NOT NULL DEFAULT 'user',
                role_assigned_at TEXT,
                role_assigned_by_tg_id INTEGER,
                card_number TEXT UNIQUE,
                card_created_at INTEGER,
                card_used_at INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_activity_ts_utc TEXT,
                notifications_enabled INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT,
                yclients_client_id INTEGER,
                phone_raw TEXT,
                phone_digits TEXT,
                phone_e164 TEXT,
                phone_ru_7 TEXT,
                phone_ru_8 TEXT,
                phone_matched_at TEXT,
                phone_match_source TEXT,
                registration_success_message_shown_at_utc TEXT
            )
            """
        )
        try:
            await db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN role_assigned_at TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN role_assigned_by_tg_id INTEGER")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN card_number TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN card_created_at INTEGER")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN card_used_at INTEGER")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN bonus_balance INTEGER NOT NULL DEFAULT 0")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_activity_ts_utc TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN notifications_enabled INTEGER NOT NULL DEFAULT 0")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_seen_at TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN yclients_client_id INTEGER")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN phone_raw TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN phone_digits TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN phone_e164 TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN phone_ru_7 TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN phone_ru_8 TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN phone_matched_at TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN phone_match_source TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN registration_success_message_shown_at_utc TEXT")
        except aiosqlite.OperationalError:
            pass
        await db.execute(
            "UPDATE users SET bonus_balance = loyalty_balance WHERE bonus_balance != loyalty_balance"
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_card_number ON users(card_number)"
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_tg_id_text ON users(CAST(user_id AS TEXT))")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_last_activity ON users(last_activity_ts_utc)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_notifications_enabled ON users(notifications_enabled)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_last_seen_at ON users(last_seen_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_phone_e164 ON users(phone_e164)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_phone_ru_7 ON users(phone_ru_7)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_phone_ru_8 ON users(phone_ru_8)")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS client_marketing_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_tg_id INTEGER NOT NULL UNIQUE,
                yclients_client_id TEXT,
                marketing_unsubscribed INTEGER NOT NULL DEFAULT 0,
                unsubscribed_at_utc TEXT,
                resubscribed_at_utc TEXT,
                unsubscribe_source TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_cmp_tg ON client_marketing_preferences(client_tg_id)")
        await db.execute("UPDATE client_marketing_preferences SET marketing_unsubscribed=0, updated_at_utc=COALESCE(updated_at_utc, created_at_utc, datetime('now')) WHERE COALESCE(marketing_unsubscribed,0)=1 AND unsubscribed_at_utc IS NULL AND COALESCE(unsubscribe_source,'')=''")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_delivery_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_tg_id INTEGER,
                yclients_client_id TEXT,
                notification_type TEXT NOT NULL,
                delivery_type TEXT NOT NULL DEFAULT 'green',
                category TEXT NOT NULL,
                funnel_type TEXT,
                source_event_id TEXT,
                decision TEXT NOT NULL,
                reason_summary TEXT,
                created_at_utc TEXT NOT NULL,
                branch_timezone TEXT,
                is_test INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        try:
            await db.execute("ALTER TABLE notification_delivery_decisions ADD COLUMN delivery_type TEXT NOT NULL DEFAULT 'green'")
        except aiosqlite.OperationalError:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ndd_client_time ON notification_delivery_decisions(client_tg_id, created_at_utc)")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS role_onboarding (
                telegram_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('manager','admin')),
                status TEXT NOT NULL CHECK(status IN ('in_progress','completed','skipped')),
                current_step INTEGER NOT NULL DEFAULT 1,
                started_at_utc TEXT NOT NULL,
                completed_at_utc TEXT,
                skipped_at_utc TEXT,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY (telegram_id, role)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_role_onboarding_status ON role_onboarding(status)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_roles (
                tg_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL CHECK(role in ('developer','manager','admin')),
                assigned_by INTEGER,
                assigned_at TEXT
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_staff_roles_role ON staff_roles(role)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_staff_roles_assigned_by ON staff_roles(assigned_by)")
        await db.execute(
            """
            INSERT INTO staff_roles (tg_id, role, assigned_by, assigned_at)
            SELECT user_id, role, role_assigned_by_tg_id, role_assigned_at
            FROM users
            WHERE role IN ('developer', 'manager', 'admin')
            ON CONFLICT(tg_id) DO NOTHING
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_role_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_tg_id INTEGER NOT NULL,
                old_role TEXT NOT NULL,
                new_role TEXT NOT NULL,
                changed_by_tg_id INTEGER NOT NULL,
                changed_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_role_audit_target_tg_id ON staff_role_audit(target_tg_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_role_audit_changed_at ON staff_role_audit(changed_at)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_tg_id INTEGER NOT NULL,
                actor_name TEXT,
                actor_role TEXT,
                action_type TEXT NOT NULL DEFAULT 'staff_action',
                human_text TEXT,
                target_tg_id INTEGER,
                target_name TEXT,
                metadata_json TEXT,
                created_at_utc TEXT,
                branch_timezone TEXT,
                action_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        for column_sql in (
            "ALTER TABLE staff_action_logs ADD COLUMN actor_name TEXT",
            "ALTER TABLE staff_action_logs ADD COLUMN actor_role TEXT",
            "ALTER TABLE staff_action_logs ADD COLUMN action_type TEXT NOT NULL DEFAULT 'staff_action'",
            "ALTER TABLE staff_action_logs ADD COLUMN human_text TEXT",
            "ALTER TABLE staff_action_logs ADD COLUMN target_name TEXT",
            "ALTER TABLE staff_action_logs ADD COLUMN metadata_json TEXT",
            "ALTER TABLE staff_action_logs ADD COLUMN created_at_utc TEXT",
            "ALTER TABLE staff_action_logs ADD COLUMN branch_timezone TEXT",
        ):
            try:
                await db.execute(column_sql)
            except aiosqlite.OperationalError:
                pass
        await db.execute(
            "UPDATE staff_action_logs SET human_text = action_text WHERE human_text IS NULL"
        )
        await db.execute(
            "UPDATE staff_action_logs SET created_at_utc = created_at WHERE created_at_utc IS NULL"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_action_logs_actor_tg_id ON staff_action_logs(actor_tg_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_action_logs_created_at ON staff_action_logs(created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_action_logs_target_tg_id ON staff_action_logs(target_tg_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_action_logs_created_at_utc ON staff_action_logs(created_at_utc)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS lost_client_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                yclients_client_id TEXT,
                client_tg_id INTEGER,
                threshold_days INTEGER NOT NULL,
                segment_key TEXT NOT NULL,
                last_visit_datetime_utc TEXT,
                last_visit_id TEXT,
                has_future_booking INTEGER NOT NULL DEFAULT 0,
                scheduled_send_at_utc TEXT,
                sent_at_utc TEXT,
                clicked_at_utc TEXT,
                status TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'yclients',
                is_test INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_lost_client_events_tg ON lost_client_events(client_tg_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_lost_client_events_threshold ON lost_client_events(threshold_days)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_lost_client_events_status ON lost_client_events(status)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS repeat_visit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                yclients_client_id TEXT,
                client_tg_id INTEGER,
                yclients_visit_id TEXT,
                yclients_service_id TEXT,
                service_name TEXT,
                last_visit_datetime_utc TEXT,
                delay_days INTEGER NOT NULL,
                scheduled_send_at_utc TEXT,
                selected_template_index INTEGER,
                selected_template_text TEXT,
                sent_at_utc TEXT,
                clicked_at_utc TEXT,
                status TEXT NOT NULL,
                branch_timezone TEXT,
                source TEXT NOT NULL DEFAULT 'yclients',
                is_test INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_repeat_visit_events_tg ON repeat_visit_events(client_tg_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_repeat_visit_events_status ON repeat_visit_events(status)")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS booking_reminder_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                yclients_record_id TEXT NOT NULL,
                yclients_client_id TEXT,
                client_tg_id INTEGER,
                client_phone TEXT,
                company_id TEXT NOT NULL,
                visit_datetime_utc TEXT NOT NULL,
                branch_timezone TEXT NOT NULL DEFAULT 'UTC',
                reminder_type TEXT NOT NULL,
                status TEXT NOT NULL,
                scheduled_at_utc TEXT NOT NULL,
                sent_at_utc TEXT,
                clicked_at_utc TEXT,
                error TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                UNIQUE(yclients_record_id, reminder_type)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bre_due ON booking_reminder_events(status, scheduled_at_utc)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bre_client ON booking_reminder_events(client_tg_id)")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_tg_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                check_sum INTEGER NULL,
                reason TEXT NULL,
                created_by_tg_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_tg_id) REFERENCES users(user_id)
            )
            """
        )
        try:
            await db.execute("ALTER TABLE transactions ADD COLUMN reason TEXT")
        except aiosqlite.OperationalError:
            pass
        cursor = await db.execute("PRAGMA table_info(transactions)")
        column_rows = await cursor.fetchall()
        await cursor.close()
        columns = {row[1] for row in column_rows}
        required_columns = {
            "user_tg_id",
            "type",
            "amount",
            "check_sum",
            "reason",
            "created_by_tg_id",
            "created_at",
        }
        if not required_columns.issubset(columns):
            await db.execute("DROP TABLE IF EXISTS transactions_legacy")
            await db.execute("ALTER TABLE transactions RENAME TO transactions_legacy")
            await db.execute(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_tg_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    check_sum INTEGER NULL,
                    reason TEXT NULL,
                    created_by_tg_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(user_tg_id) REFERENCES users(user_id)
                )
                """
            )
            legacy_cursor = await db.execute("PRAGMA table_info(transactions_legacy)")
            legacy_rows = await legacy_cursor.fetchall()
            await legacy_cursor.close()
            legacy_columns = {row[1] for row in legacy_rows}
            user_expr = "user_tg_id" if "user_tg_id" in legacy_columns else "user_id"
            amount_expr = "amount" if "amount" in legacy_columns else "bonus_change"
            check_sum_expr = "check_sum" if "check_sum" in legacy_columns else "check_amount"
            reason_expr = "reason" if "reason" in legacy_columns else "NULL"
            created_by_expr = (
                "created_by_tg_id" if "created_by_tg_id" in legacy_columns else "0"
            )
            if "created_at" in legacy_columns:
                created_at_expr = "COALESCE(CAST(strftime('%s', created_at) AS INTEGER), 0)"
            else:
                created_at_expr = "0"
            query = f"""
                INSERT INTO transactions (
                    user_tg_id,
                    type,
                    amount,
                    check_sum,
                    reason,
                    created_by_tg_id,
                    created_at
                )
                SELECT
                    {user_expr},
                    type,
                    {amount_expr},
                    {check_sum_expr},
                    {reason_expr},
                    {created_by_expr},
                    {created_at_expr}
                FROM transactions_legacy
                """
            await db.execute(query)
            await db.execute("DROP TABLE transactions_legacy")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_user_tg_id ON transactions(user_tg_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS loyalty_codes (
                code TEXT PRIMARY KEY,
                client_id INTEGER NOT NULL,
                created_at_ts INTEGER NOT NULL,
                expires_at_ts INTEGER NOT NULL,
                used_at_ts INTEGER NULL,
                used_by_staff_id INTEGER NULL,
                used_action TEXT NULL,
                FOREIGN KEY(client_id) REFERENCES users(user_id)
            )
            """
        )
        try:
            await db.execute("ALTER TABLE loyalty_codes ADD COLUMN created_at_ts INTEGER")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE loyalty_codes ADD COLUMN expires_at_ts INTEGER")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE loyalty_codes ADD COLUMN used_at_ts INTEGER")
        except aiosqlite.OperationalError:
            pass
        loyalty_cursor = await db.execute("PRAGMA table_info(loyalty_codes)")
        loyalty_rows = await loyalty_cursor.fetchall()
        await loyalty_cursor.close()
        loyalty_columns = {row[1] for row in loyalty_rows}
        has_created_at = "created_at" in loyalty_columns
        has_expires_at = "expires_at" in loyalty_columns
        has_used_at = "used_at" in loyalty_columns
        if "created_at_ts" in loyalty_columns and has_created_at:
            await db.execute(
                """
                UPDATE loyalty_codes
                SET created_at_ts = CASE
                    WHEN created_at_ts IS NOT NULL THEN created_at_ts
                    WHEN typeof(created_at) = 'integer' THEN created_at
                    ELSE COALESCE(CAST(strftime('%s', created_at) AS INTEGER), 0)
                END
                WHERE created_at_ts IS NULL
                """
            )
        if "expires_at_ts" in loyalty_columns and has_expires_at:
            await db.execute(
                """
                UPDATE loyalty_codes
                SET expires_at_ts = CASE
                    WHEN expires_at_ts IS NOT NULL THEN expires_at_ts
                    WHEN typeof(expires_at) = 'integer' THEN expires_at
                    ELSE COALESCE(CAST(strftime('%s', expires_at) AS INTEGER), 0)
                END
                WHERE expires_at_ts IS NULL
                """
            )
        if "expires_at_ts" in loyalty_columns:
            await db.execute(
                """
                UPDATE loyalty_codes
                SET expires_at_ts = created_at_ts + 600
                WHERE expires_at_ts IS NULL
                  AND created_at_ts IS NOT NULL
                """
            )
        if "used_at_ts" in loyalty_columns and has_used_at:
            await db.execute(
                """
                UPDATE loyalty_codes
                SET used_at_ts = CASE
                    WHEN used_at_ts IS NOT NULL THEN used_at_ts
                    WHEN typeof(used_at) = 'integer' THEN used_at
                    ELSE COALESCE(CAST(strftime('%s', used_at) AS INTEGER), NULL)
                END
                WHERE used_at_ts IS NULL AND used_at IS NOT NULL
                """
            )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_loyalty_codes_client_id ON loyalty_codes(client_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_loyalty_codes_expires_at_ts ON loyalty_codes(expires_at_ts)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                phone TEXT,
                event_type TEXT NOT NULL,
                event_name TEXT NOT NULL,
                screen TEXT,
                payload_json TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc TEXT NOT NULL,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_events_user_id ON user_events(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_events_phone ON user_events(phone)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_events_username ON user_events(username)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_events_ts_utc ON user_events(ts_utc)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_logs_ts_utc ON bot_logs(ts_utc)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_logs_level ON bot_logs(level)")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS error_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                error_type TEXT NOT NULL,
                where_text TEXT NOT NULL,
                count INTEGER NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                last_context_json TEXT
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_error_events_last_seen ON error_events(last_seen)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_error_events_count ON error_events(count)")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS message_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_ts TEXT NOT NULL,
                updated_ts TEXT NOT NULL,
                last_staff_id INTEGER NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS thread_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                sender_role TEXT NOT NULL,
                staff_id INTEGER NULL,
                text TEXT NOT NULL,
                tg_message_id INTEGER NULL,
                FOREIGN KEY(thread_id) REFERENCES message_threads(id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                staff_id INTEGER NOT NULL,
                segment TEXT NOT NULL DEFAULT 'all',
                payload_type TEXT NOT NULL DEFAULT 'text',
                text TEXT NOT NULL,
                recipients_total INTEGER NOT NULL,
                delivered INTEGER NOT NULL,
                failed INTEGER NOT NULL,
                blocked INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        try:
            await db.execute("ALTER TABLE broadcast_logs ADD COLUMN segment TEXT NOT NULL DEFAULT 'all'")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE broadcast_logs ADD COLUMN payload_type TEXT NOT NULL DEFAULT 'text'")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE broadcast_logs ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")
        except aiosqlite.OperationalError:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_message_threads_user_id ON message_threads(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_message_threads_status ON message_threads(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_message_threads_updated_ts ON message_threads(updated_ts)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_thread_messages_thread_id ON thread_messages(thread_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_logs_staff_id ON broadcast_logs(staff_id)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_by_tg_id INTEGER NOT NULL,
                segment TEXT NOT NULL,
                message_type TEXT NOT NULL,
                text TEXT,
                file_id TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broadcast_id INTEGER NOT NULL,
                tg_user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error_short TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(broadcast_id) REFERENCES broadcasts(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_recipients_broadcast_id ON broadcast_recipients(broadcast_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_recipients_status ON broadcast_recipients(status)")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_by_tg_id INTEGER NOT NULL,
                created_by_role TEXT,
                audience_key TEXT NOT NULL,
                audience_name TEXT NOT NULL,
                text TEXT NOT NULL,
                photo_file_id TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                total_count INTEGER NOT NULL DEFAULT 0,
                sent_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                blocked_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                created_at_utc TEXT NOT NULL,
                sent_at_utc TEXT,
                branch_timezone TEXT,
                branch_local_sent_at TEXT,
                is_test INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_recipient_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                recipient_tg_id INTEGER,
                yclients_client_id TEXT,
                status TEXT NOT NULL,
                error_code TEXT,
                error_summary TEXT,
                sent_at_utc TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                FOREIGN KEY(campaign_id) REFERENCES broadcast_campaigns(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_campaigns_created_at ON broadcast_campaigns(created_at_utc DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_recipient_logs_campaign_id ON broadcast_recipient_logs(campaign_id)")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_test_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                target_tg_id INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'dev_test',
                is_test INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT,
                status TEXT NOT NULL DEFAULT 'created',
                created_at_utc TEXT NOT NULL,
                sent_at_utc TEXT,
                error_summary TEXT
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_notification_test_events_target ON notification_test_events(target_tg_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_notification_test_events_test ON notification_test_events(is_test, source)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_ts_utc TEXT NOT NULL,
                created_ts_local TEXT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                phone TEXT,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                guests INTEGER NOT NULL,
                comment TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                status_ts_utc TEXT,
                status_by_staff_id INTEGER,
                status_reason TEXT,
                notified_hostess_ts_utc TEXT,
                last_reminder_ts_utc TEXT,
                reminder_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
            """
        )
        try:
            await db.execute("ALTER TABLE bookings ADD COLUMN created_ts_local TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE bookings ADD COLUMN status_ts_utc TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE bookings ADD COLUMN status_by_staff_id INTEGER")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE bookings ADD COLUMN status_reason TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE bookings ADD COLUMN notified_hostess_ts_utc TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE bookings ADD COLUMN last_reminder_ts_utc TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE bookings ADD COLUMN reminder_count INTEGER NOT NULL DEFAULT 0")
        except aiosqlite.OperationalError:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_user_id ON bookings(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_created_ts_utc ON bookings(created_ts_utc)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_created_ts_local ON bookings(created_ts_local)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bookings_last_reminder_ts_utc ON bookings(last_reminder_ts_utc)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc TEXT NOT NULL,
                ts_local TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                text TEXT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                closed_by INTEGER NULL,
                closed_ts_utc TEXT NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id INTEGER NOT NULL,
                ts_utc TEXT NOT NULL,
                admin_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                FOREIGN KEY(feedback_id) REFERENCES feedback(id)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user_id ON feedback(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_ts_utc ON feedback(ts_utc)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status)")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS yclients_settings (
                id INTEGER PRIMARY KEY CHECK(id=1),
                company_id TEXT,
                partner_token TEXT,
                user_token TEXT,
                base_url TEXT,
                updated_at TEXT,
                updated_by_tg_id INTEGER
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS booking_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER NOT NULL,
                yclients_record_id TEXT NOT NULL,
                company_id TEXT,
                service_id TEXT,
                staff_id TEXT,
                datetime_iso TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                raw_payload_json TEXT,
                FOREIGN KEY(tg_user_id) REFERENCES users(user_id)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_booking_links_tg_user_id ON booking_links(tg_user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_booking_links_record_id ON booking_links(yclients_record_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_booking_links_status ON booking_links(status)"
        )


        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_attribution (
                company_id TEXT NOT NULL,
                record_id TEXT PRIMARY KEY,
                client_id TEXT,
                source TEXT NOT NULL,
                created_via TEXT,
                created_at TEXT NOT NULL,
                original_record_id TEXT,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_attribution_company_id ON telegram_attribution(company_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_attribution_client_id ON telegram_attribution(client_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_attribution_source ON telegram_attribution(source)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_attribution_active ON telegram_attribution(is_active)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS master_photos (
                company_id TEXT NOT NULL,
                staff_id TEXT NOT NULL,
                staff_name TEXT,
                telegram_file_id TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by_tg_id INTEGER,
                PRIMARY KEY (company_id, staff_id)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_master_photos_company_id ON master_photos(company_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_master_photos_updated_at ON master_photos(updated_at)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS contacts_override (
                company_id TEXT PRIMARY KEY,
                address TEXT,
                phone TEXT,
                schedule TEXT,
                updated_at TEXT NOT NULL,
                updated_by_tg_id INTEGER
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_contacts_override_updated_at ON contacts_override(updated_at)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS company_runtime_settings (
                company_id TEXT PRIMARY KEY,
                city TEXT,
                timezone TEXT,
                source TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_company_runtime_settings_updated_at ON company_runtime_settings(updated_at)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS support_settings (
                company_id TEXT PRIMARY KEY,
                support_description TEXT,
                support_username TEXT,
                updated_at TEXT NOT NULL,
                updated_by_tg_id INTEGER
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_support_settings_updated_at ON support_settings(updated_at)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS client_segment_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                segment_key TEXT NOT NULL,
                segment_filter_json TEXT,
                client_count INTEGER NOT NULL DEFAULT 0,
                calculated_at_utc TEXT NOT NULL,
                branch_timezone TEXT,
                error_summary TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_client_segment_cache_key_filter ON client_segment_cache(segment_key, segment_filter_json)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS automation_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_by_tg_id INTEGER,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_automation_settings_updated_at ON automation_settings(updated_at_utc)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS post_visit_feedback_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                yclients_record_id TEXT NOT NULL,
                yclients_client_id TEXT,
                client_tg_id INTEGER,
                client_name TEXT,
                client_phone TEXT,
                staff_id TEXT,
                staff_name TEXT,
                service_id TEXT,
                service_name TEXT,
                visit_datetime_utc TEXT,
                branch_timezone TEXT,
                status TEXT NOT NULL,
                rating INTEGER,
                client_comment TEXT,
                admin_reply TEXT,
                sent_at_utc TEXT,
                rated_at_utc TEXT,
                comment_at_utc TEXT,
                admin_replied_at_utc TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                is_test INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'yclients'
            )
            """
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_post_visit_feedback_record_source_test ON post_visit_feedback_events(yclients_record_id, source, is_test)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_post_visit_feedback_status ON post_visit_feedback_events(status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_post_visit_feedback_client_tg ON post_visit_feedback_events(client_tg_id)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS cancellation_recovery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                yclients_record_id TEXT NOT NULL,
                yclients_client_id TEXT,
                client_tg_id INTEGER,
                staff_id TEXT,
                staff_name TEXT,
                service_id TEXT,
                service_name TEXT,
                cancelled_booking_datetime_utc TEXT,
                cancellation_detected_at_utc TEXT NOT NULL,
                scheduled_send_at_utc TEXT,
                branch_timezone TEXT,
                status TEXT NOT NULL,
                sent_at_utc TEXT,
                clicked_at_utc TEXT,
                error_summary TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                is_test INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'yclients'
            )
            """
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_cancel_recovery_record_source_test ON cancellation_recovery_events(yclients_record_id, source, is_test)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_cancel_recovery_status ON cancellation_recovery_events(status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_cancel_recovery_client_tg ON cancellation_recovery_events(client_tg_id)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS birthday_funnel_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                yclients_client_id TEXT,
                client_tg_id INTEGER,
                birth_date TEXT,
                birthday_year INTEGER NOT NULL,
                scheduled_send_at_utc TEXT,
                sent_at_utc TEXT,
                clicked_at_utc TEXT,
                yclients_booking_id TEXT,
                status TEXT NOT NULL,
                branch_timezone TEXT,
                source TEXT NOT NULL DEFAULT 'local_db',
                is_test INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_birthday_funnel_client_year_test ON birthday_funnel_events(client_tg_id, birthday_year, is_test)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_attributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_event_id INTEGER,
                campaign_id INTEGER,
                funnel_type TEXT NOT NULL,
                client_tg_id INTEGER,
                yclients_client_id TEXT,
                click_at_utc TEXT,
                booking_created_at_utc TEXT,
                yclients_booking_id TEXT,
                attributed_revenue REAL,
                attribution_window_days INTEGER NOT NULL DEFAULT 7,
                status TEXT NOT NULL DEFAULT 'clicked',
                is_test INTEGER NOT NULL DEFAULT 0,
                source TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_notification_attr_client_tg ON notification_attributions(client_tg_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_notification_attr_yc_client ON notification_attributions(yclients_client_id)")
        try:
            await db.execute("ALTER TABLE notification_attributions ADD COLUMN source TEXT")
        except Exception:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_notification_attr_click ON notification_attributions(click_at_utc)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_attr_booking ON notification_attributions(yclients_booking_id) WHERE yclients_booking_id IS NOT NULL")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS loyalty_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_tg_id INTEGER NOT NULL,
                yclients_visit_or_record_id TEXT NOT NULL,
                yclients_client_id TEXT NULL,
                action_type TEXT NOT NULL,
                value TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                error_short TEXT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_loyalty_actions_staff_tg_id ON loyalty_actions(staff_tg_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_loyalty_actions_record_id ON loyalty_actions(yclients_visit_or_record_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_loyalty_actions_created_at ON loyalty_actions(created_at)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_codes (
                user_tg_id INTEGER PRIMARY KEY,
                referral_code TEXT NOT NULL UNIQUE,
                created_at_utc TEXT NOT NULL,
                FOREIGN KEY(user_tg_id) REFERENCES users(user_id)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_referral_codes_referral_code ON referral_codes(referral_code)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_attributions (
                invited_tg_id INTEGER PRIMARY KEY,
                inviter_tg_id INTEGER NOT NULL,
                referral_code TEXT NOT NULL,
                attributed_at_utc TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                invited_yclients_client_id TEXT,
                qualifying_record_id TEXT,
                qualifying_visit_at_utc TEXT,
                rewarded_at_utc TEXT,
                invited_had_paid_before INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(invited_tg_id) REFERENCES users(user_id),
                FOREIGN KEY(inviter_tg_id) REFERENCES users(user_id)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_referral_attributions_inviter ON referral_attributions(inviter_tg_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_referral_attributions_status ON referral_attributions(status)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS loyalty_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_tg_id INTEGER NOT NULL,
                yclients_client_id TEXT,
                operation_type TEXT NOT NULL,
                points_delta INTEGER NOT NULL,
                reason TEXT NOT NULL,
                source TEXT NOT NULL,
                source_event_id TEXT,
                created_at_utc TEXT NOT NULL,
                branch_timezone TEXT,
                resulting_balance INTEGER,
                FOREIGN KEY(user_tg_id) REFERENCES users(user_id)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_loyalty_operations_user ON loyalty_operations(user_tg_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_loyalty_operations_created ON loyalty_operations(created_at_utc)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_loyalty_operations_source_event ON loyalty_operations(source, source_event_id)")

        await db.commit()


async def execute(query: str, params: Iterable[Any] | None = None) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute(query, params or ())
        await db.commit()


async def fetchone(query: str, params: Iterable[Any] | None = None) -> aiosqlite.Row | None:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON;")
        cursor = await db.execute(query, params or ())
        row = await cursor.fetchone()
        await cursor.close()
        return row


async def fetchall(query: str, params: Iterable[Any] | None = None) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON;")
        cursor = await db.execute(query, params or ())
        rows = await cursor.fetchall()
        await cursor.close()
        return rows
