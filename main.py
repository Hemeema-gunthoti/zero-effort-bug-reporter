"""
main.py
--------
The main orchestrator — called by the CI/CD pipeline after tests run.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import glob
import json
import asyncio
from agent.failure_agent import FailureAnalysisAgent
from agent.mcp_client import JiraMCPClient

ARTIFACT_DIR = os.path.join(os.path.dirname(__file__), "artifacts")


async def process_failures():
    """Process all captured test failures end-to-end."""

    # Step 1 — find all failure metadata files
    pattern        = os.path.join(ARTIFACT_DIR, "failure_*.json")
    metadata_files = glob.glob(pattern)

    if not metadata_files:
        print("✅ No test failures found. Nothing to report.")
        return

    print(f"\n{'='*60}")
    print(f"🚀 ZERO-EFFORT BUG REPORTER STARTING")
    print(f"{'='*60}")
    print(f"Found {len(metadata_files)} failure(s) to process\n")

    # Step 2 — initialize AI agent
    agent   = FailureAnalysisAgent()
    results = []

    for i, metadata_path in enumerate(metadata_files, 1):
        print(f"\n[{i}/{len(metadata_files)}] Processing: {os.path.basename(metadata_path)}")

        try:
            # AI analysis
            agent_result = agent.analyze_failure(metadata_path)
            bug_report   = agent_result["bug_report"]

            # Jira ticket via MCP
            async with JiraMCPClient() as jira:
                jira_result = await jira.create_jira_ticket(bug_report)

            # Email notification (skipped until Layer 6)
            _send_notification(bug_report, jira_result)

            results.append({
                "test":     bug_report["metadata"]["test_name"],
                "ticket":   jira_result.get("ticket_key", "N/A"),
                "url":      jira_result.get("ticket_url",  "N/A"),
                "status":   jira_result.get("status",      "unknown"),
                "severity": bug_report["severity"],
            })

        except Exception as e:
            print(f"❌ Failed to process {os.path.basename(metadata_path)}: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "test":   metadata_path,
                "ticket": "ERROR",
                "status": str(e),
            })

    # Step 3 — final summary
    print(f"\n{'='*60}")
    print(f"📋 FINAL SUMMARY")
    print(f"{'='*60}")
    for r in results:
        icon = "✅" if r.get("status") in ("success", "duplicate") else "❌"
        print(f"{icon} {r.get('test', 'unknown')[:60]}")
        print(f"   Ticket   : {r.get('ticket')} — {r.get('url', '')}")
        print(f"   Status   : {r.get('status')}")
        if r.get("severity"):
            print(f"   Severity : {r.get('severity')}")
    print(f"{'='*60}")


def _send_notification(bug_report: dict, jira_result: dict):
    """
    Email notification placeholder.
    Replace with real call when Layer 6 is implemented:
      from notifications.email_notifier import notify_email
      notify_email(bug_report, jira_result)
    """
    ticket = jira_result.get("ticket_key", "N/A")
    print(f"📧 Email notification skipped (Layer 6 not yet implemented) — ticket: {ticket}")


if __name__ == "__main__":
    asyncio.run(process_failures())