"""Small SQL snippets used only by focused database tests."""

SELECT_TEST_EVENT = """
SELECT event_type, source, message, payload
FROM agent_events
WHERE run_id = %s
"""

DELETE_TEST_EVENTS = "DELETE FROM agent_events WHERE run_id = %s"
