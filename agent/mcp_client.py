"""
agent/mcp_client.py
--------------------
Direct Jira client — replaces the MCP stdio transport which has
known compatibility issues with Python 3.12 + Windows asyncio.

All the same tool logic is preserved:
  - duplicate detection before creating
  - create ticket
  - attach screenshot + stack trace
  - add comment on duplicate

The MCP server (mcp_server/server.py) still exists and works fine
in GitHub Actions (Linux). This file is the local Windows-compatible
equivalent that calls jira_tools directly.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
from mcp_server.jira_tools import (
    create_bug,
    search_similar_bugs,
    add_comment,
    attach_file,
)


class JiraMCPClient:
    """
    Drop-in replacement for the async MCP client.
    Uses the same async interface (async with / await) so main.py
    needs zero changes — but calls jira_tools directly instead of
    going through subprocess stdio.
    """

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def create_jira_ticket(self, bug_report: dict) -> dict:
        """
        Full flow:
          1. Search for duplicates
          2. If duplicate → add comment, return duplicate status
          3. If new → create ticket + attach files
        """
        project_key = bug_report.get("jira_project", "AUTO")

        # ── Step 1: Duplicate check ───────────────────────────────────
        print("\n🔍 Checking for duplicate tickets...")
        search_result = search_similar_bugs(
            project_key = project_key,
            search_text = bug_report["title"][:50],
        )

        if search_result.get("total", 0) > 0:
            existing = search_result["issues"][0]
            print(f"⚠️  Duplicate found: {existing['key']} — {existing['summary']}")
            print(f"   Adding comment instead of creating new ticket...")

            comment = (
                f"h3. 🤖 Automated Test Failure (Duplicate)\n\n"
                f"This failure was detected again:\n\n"
                f"* Test: {bug_report['metadata']['test_name']}\n"
                f"* Timestamp: {bug_report['metadata']['timestamp']}\n"
                f"* Severity: {bug_report['severity']}\n\n"
                f"_Added automatically by Zero-Effort Bug Reporter_"
            )

            add_comment(
                ticket_key = existing["key"],
                comment    = comment,
            )

            return {
                "status":     "duplicate",
                "ticket_key": existing["key"],
                "ticket_url": existing["url"],
                "message":    f"Duplicate of {existing['key']} — comment added",
            }

        # ── Step 2: Create the ticket ─────────────────────────────────
        print("✅ No duplicates found — creating new ticket...")

        create_result = create_bug(
            project_key = project_key,
            title       = bug_report["title"],
            description = bug_report["description"],
            severity    = bug_report["severity"],
            priority    = bug_report["priority"],
            # Use accountId — required by Jira Cloud
            assignee    = bug_report.get("assignee_account_id") or None,
            labels      = bug_report.get("labels", []),
            components  = bug_report.get("components", []),
        )

        if create_result.get("status") != "success":
            print(f"❌ Jira ticket creation failed: {create_result.get('message')}")
            print(f"   Details: {create_result.get('details', 'none')}")
            return create_result

        ticket_key = create_result["ticket_key"]
        print(f"✅ Ticket created: {ticket_key}")
        print(f"   URL: {create_result['ticket_url']}")

        # ── Step 3: Attach screenshot ─────────────────────────────────
        screenshot = bug_report.get("attachments", {}).get("screenshot")
        if screenshot and os.path.exists(screenshot):
            print(f"📎 Attaching screenshot...")
            result = attach_file(
                ticket_key = ticket_key,
                file_path  = screenshot,
            )
            print(f"   {result.get('message', 'done')}")

        # ── Step 4: Attach stack trace log ────────────────────────────
        stacktrace_file = bug_report.get("attachments", {}).get("stacktrace")
        if stacktrace_file and os.path.exists(stacktrace_file):
            print(f"📎 Attaching stack trace log...")
            result = attach_file(
                ticket_key = ticket_key,
                file_path  = stacktrace_file,
            )
            print(f"   {result.get('message', 'done')}")

        return create_result