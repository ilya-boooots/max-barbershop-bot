# Manual smoke: booking reminders

1. Create a test booking more than 48h ahead for a MAX-attributed user.
2. In a safe test environment, simulate current branch time or move the booking into the 48h reminder window.
3. Verify the 48h confirmation reminder is sent once with the Telegram text.
4. Run the reminder loop again and verify the 48h reminder is not duplicated.
5. Move the same booking or create another attributed booking near the 2h reminder window.
6. Verify the 2h reminder is sent once with the Telegram text, master, service, date/time, and address when available.
7. Cancel a booking before its reminder window.
8. Verify the reminder is skipped and no user message is sent.
9. Reschedule a booking using the product flow.
10. Verify the old record is skipped/canceled and the new attributed record follows its new schedule.
11. Mark the user as blocked/stopped or simulate a blocked/stopped MAX sender result.
12. Verify future reminders skip that user, notifications are disabled, and notification history/delivery rows are saved.
