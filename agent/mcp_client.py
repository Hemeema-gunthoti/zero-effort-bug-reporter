"""
agent/mcp_client.py
-------------------
Direct Jira client with duplicate detection and priority escalation.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mcp_server.jira_tools import (
    create_bug,
    search_similar_bugs,
    add_comment,
    attach_file,
    escalate_priority,
    get_ticket,
)


class JiraMCPClient:

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def create_jira_ticket(
        self,
        bug_report: dict,
        cached_ticket_key: str = None,  # passed from main.py when cache hits
    ) -> dict:
        project_key = bug_report.get("jira_project", "AUTO")
        test_name = bug_report["metadata"]["test_name"]
        component = bug_report["metadata"].get("affected_component", "unknown")
        error_type = bug_report["metadata"].get("error_type")

        # ── Fast path: cache gave us the ticket key directly ──────────
        # Skip Jira label search entirely — use the cached key to fetch
        # the ticket's current priority, then escalate + comment.
        if cached_ticket_key:
            print(f"\n⚡ Fast path — using cached ticket {cached_ticket_key}")
            return await self._handle_duplicate(
                bug_report = bug_report,
                ticket_key = cached_ticket_key,
                match_method = "file_cache",
            )

        # ── Step 1: Jira label search (no cache available) ────────────
        # FIX: Pass error_type to search_similar_bugs for proper deduplication
        print("\n🔍 Checking for duplicate tickets...")
        search_result = search_similar_bugs(
            project_key = project_key,
            test_name = test_name,
            component = component,
            error_type = error_type,
        )

        if search_result.get("total", 0) > 0:
            existing = search_result["issues"][0]
            match_method = search_result.get("match_method", "unknown")
            return await self._handle_duplicate(
                bug_report = bug_report,
                ticket_key = existing["key"],
                match_method = match_method,
                existing = existing,
            )

        # ── Step 2: Create new ticket ─────────────────────────────────
        print("✅ No duplicates found — creating new ticket...")

        create_result = create_bug(
            project_key = project_key,
            title = bug_report["title"],
            description = bug_report["description"],
            severity = bug_report["severity"],
            priority = bug_report["priority"],
            assignee = bug_report.get("assignee_account_id") or None,
            labels = bug_report.get("labels", []),
            components = bug_report.get("components", []),
        )

        if create_result.get("status") != "success":
            print(f"❌ Jira ticket creation failed: {create_result.get('message')}")
            print(f" Details: {create_result.get('details', 'none')}")
            return create_result

        ticket_key = create_result["ticket_key"]
        print(f"✅ Ticket created: {ticket_key}")
        print(f" URL: {create_result['ticket_url']}")

        # ── Step 3: Attach screenshot ─────────────────────────────────
        screenshot = bug_report.get("attachments", {}).get("screenshot")
        if screenshot and os.path.exists(screenshot):
            print(f"📎 Attaching screenshot...")
            result = attach_file(ticket_key=ticket_key, file_path=screenshot)
            print(f" {result.get('message', 'done')}")

        # ── Step 4: Attach stack trace ────────────────────────────────
        stacktrace_file = bug_report.get("attachments", {}).get("stacktrace")
        if stacktrace_file and os.path.exists(stacktrace_file):
            print(f"📎 Attaching stack trace log...")
            result = attach_file(ticket_key=ticket_key, file_path=stacktrace_file)
            print(f" {result.get('message', 'done')}")

        return create_result

    async def _handle_duplicate(
        self,
        bug_report: dict,
        ticket_key: str,
        match_method: str,
        existing: dict = None,
    ) -> dict:
        """
        Shared logic for both cache-hit and Jira-search-hit paths.
        Fetches current priority, escalates, adds comment, attaches files.
        """
        # Fetch current priority from Jira if not already known
        if existing and existing.get("priority"):
            old_priority = existing["priority"]
            ticket_url = existing.get("url", "")
        else:
            print(f" Fetching current state of {ticket_key} from Jira...")
            ticket_data = get_ticket(ticket_key)
            old_priority = ticket_data.get("priority", "Medium")
            ticket_url = ticket_data.get("url", "")

        print(f"⚠️ Duplicate ({match_method}): {ticket_key} — priority={old_priority}")

        # ── Escalate priority ─────────────────────────────────────────
        print(f"⬆️  Escalating priority: {old_priority} → next level...")
        escalate_result = escalate_priority(
            ticket_key = ticket_key,
            current_priority = old_priority,
        )
        print(f" {escalate_result['message']}")

        # ── Add recurrence comment ────────────────────────────────────
        comment = self._build_recurrence_comment(
            bug_report = bug_report,
            old_priority = old_priority,
            new_priority = escalate_result.get("new_priority", old_priority),
            escalated = escalate_result.get("status") == "success",
        )
        add_comment(ticket_key=ticket_key, comment=comment)
        print(f"💬 Recurrence comment added to {ticket_key}")

        # ── Attach new screenshot ─────────────────────────────────────
        screenshot = bug_report.get("attachments", {}).get("screenshot")
        if screenshot and os.path.exists(screenshot):
            print(f"📎 Attaching screenshot to {ticket_key}...")
            result = attach_file(ticket_key=ticket_key, file_path=screenshot)
            print(f" {result.get('message', 'done')}")

        # ── Attach new stack trace ────────────────────────────────────
        stacktrace_file = bug_report.get("attachments", {}).get("stacktrace")
        if stacktrace_file and os.path.exists(stacktrace_file):
            print(f"📎 Attaching stack trace to {ticket_key}...")
            result = attach_file(ticket_key=ticket_key, file_path=stacktrace_file)
            print(f" {result.get('message', 'done')}")

        return {
            "status": "duplicate",
            "ticket_key": ticket_key,
            "ticket_url": ticket_url,
            "old_priority": old_priority,
            "new_priority": escalate_result.get("new_priority", old_priority),
            "message": f"Duplicate of {ticket_key} — priority escalated + comment added",
        }

    def _build_recurrence_comment(
        self,
        bug_report: dict,
        old_priority: str,
        new_priority: str,
        escalated: bool,
    ) -> str:
        metadata = bug_report["metadata"]
        escalation_note = (
            f"Priority escalated from *{old_priority}* to *{new_priority}*"
            if escalated
            else f"Already at maximum priority (*{old_priority}*)"
        )

        return (
            f"h3. 🔴 Test Failure Recurrence Detected\n\n"
            f"This bug has been detected *again* by the automated test suite.\n\n"
            f"|| Field || Value ||\n"
            f"| Test | {metadata['test_name']} |\n"
            f"| Timestamp | {metadata['timestamp']} |\n"
            f"| Severity | {bug_report['severity']} |\n"
            f"| Duration | {metadata.get('duration', 'N/A')}s |\n"
            f"| Error Type | {metadata.get('error_type', 'N/A')} |\n\n"
            f"*Priority Update:* {escalation_note}\n\n"
            f"This issue has been detected multiple times and remains unresolved. "
            f"Please prioritize a fix.\n\n"
            f"_Added automatically by Zero-Effort Bug Reporter_"
        )