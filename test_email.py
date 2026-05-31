# test_email.py

from notifications.email_notifier import notify_email

bug_report = {
    "title": "Login failure",
    "severity": "High",
    "priority": "P1",
    "metadata": {
        "test_name": "test_login",
        "affected_component": "Auth",
        "timestamp": "2026-05-31 12:00"
    }
}

jira_result = {
    "status": "success",
    "ticket_key": "BUG-123",
    "ticket_url": "https://jira.example.com/browse/BUG-123"
}

notify_email(bug_report, jira_result)