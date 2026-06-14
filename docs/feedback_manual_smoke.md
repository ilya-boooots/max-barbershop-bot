# Post-visit feedback manual smoke

1. Create a booking attributed to MAX.
2. Mark/observe the visit as completed in YClients.
3. Run the reminder/feedback loop.
4. Verify the feedback request sends once.
5. Submit rating 5.
6. Verify the response is saved and no admin alert is sent.
7. Submit rating 1/2/3 on another completed booking.
8. Verify admin/manager/developer alert is sent.
9. Run the loop again.
10. Verify no duplicate request is sent.
11. Cancel a booking and verify no feedback request is sent.
12. Simulate blocked/stopped user and verify notifications are disabled.
