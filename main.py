"""
main.py
--------
Main orchestrator — finds all failure artifacts and processes them.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import glob
import json
import asyncio
from datetime import datetime, timedelta
from agent.failure_agent import FailureAnalysisAgent
from agent.mcp_client    import JiraMCPClient

ARTIFACT_DIR = os.path.join(os.path.dirname(__file__), "artifacts")
CACHE_FILE   = os.path.join(ARTIFACT_DIR, "dedup_cache.json")
TTL_HOURS    = 24


# ── File-based dedup cache ─────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def _cache_key(test_name: str) -> str:
    safe = test_name.lower()
    for ch in " /\\.[]():":
        safe = safe.replace(ch, "_")
    return safe[:200]


def _is_cached(test_name: str, cache: dict) -> bool:
    entry = cache.get(_cache_key(test_name))
    if not entry:
        return False
    reported_at = datetime.fromisoformat(entry["timestamp"])
    if datetime.now() - reported_at > timedelta(hours=TTL_HOURS):
        del cache[_cache_key(test_name)]
        return False
    return True


def _get_cached_ticket(test_name: str, cache: dict) -> str | None:
    entry = cache.get(_cache_key(test_name))
    if not entry:
        return None
    reported_at = datetime.fromisoformat(entry["timestamp"])
    if datetime.now() - reported_at > timedelta(hours=TTL_HOURS):
        return None
    return entry.get("ticket_key")


def _mark_cached(test_name: str, ticket_key: str, cache: dict) -> None:
    cache[_cache_key(test_name)] = {
        "ticket_key": ticket_key,
        "timestamp":  datetime.now().isoformat(),
        "test_name":  test_name,
    }
    _save_cache(cache)
    print(f"   💾 Cache: stored {test_name[:60]} → {ticket_key}")


# ── Main pipeline ──────────────────────────────────────────────────────

async def process_failures():

    pattern        = os.path.join(ARTIFACT_DIR, "failure_*.json")
    metadata_files = sorted(glob.glob(pattern))

    if not metadata_files:
        print("✅ No test failures found. Nothing to report.")
        return

    print(f"\n{'='*60}")
    print(f"🚀 ZERO-EFFORT BUG REPORTER STARTING")
    print(f"{'='*60}")
    print(f"Found {len(metadata_files)} failure(s) to process")

    agent              = FailureAnalysisAgent()
    results            = []
    processed_this_run = {}   # test_name → ticket_key within this run
    cache              = _load_cache()

    for i, metadata_path in enumerate(metadata_files, 1):
        print(f"\n[{i}/{len(metadata_files)}] Processing: {os.path.basename(metadata_path)}")

        try:
            agent_result = agent.analyze_failure(metadata_path)
            bug_report   = agent_result["bug_report"]
            test_name    = bug_report["metadata"]["test_name"]

            # ── Layer 1: same-run guard ───────────────────────────────
            if test_name in processed_this_run:
                existing_key = processed_this_run[test_name]
                print(f"⚠️  [Layer 1] Same-run duplicate — already processed as {existing_key}")
                results.append({
                    "test":     test_name,
                    "ticket":   existing_key,
                    "url":      "",
                    "status":   "skipped-same-run",
                    "severity": bug_report["severity"],
                })
                continue

            # ── Layer 2: file cache check ─────────────────────────────
            if _is_cached(test_name, cache):
                cached_key = _get_cached_ticket(test_name, cache)
                print(f"⚠️  [Layer 2] Cache hit — reported as {cached_key} within last 24h")
                print(f"   Escalating priority + adding recurrence comment...")

                async with JiraMCPClient() as jira:
                    jira_result = await jira.create_jira_ticket(
                        bug_report        = bug_report,
                        cached_ticket_key = cached_key,   # ← skip Jira search, go direct
                    )

                ticket_key = jira_result.get("ticket_key", cached_key or "N/A")
                processed_this_run[test_name] = ticket_key
                _send_notification(bug_report, jira_result)
                results.append({
                    "test":     test_name,
                    "ticket":   ticket_key,
                    "url":      jira_result.get("ticket_url", ""),
                    "status":   jira_result.get("status", "duplicate"),
                    "severity": bug_report["severity"],
                })
                continue

            # ── Layer 3: Jira API (creates or escalates) ──────────────
            async with JiraMCPClient() as jira:
                jira_result = await jira.create_jira_ticket(bug_report)

            ticket_key = jira_result.get("ticket_key", "N/A")
            status     = jira_result.get("status", "unknown")

            # Cache after any successful outcome
            if status in ("success", "duplicate") and ticket_key not in ("N/A", "ERROR"):
                _mark_cached(test_name, ticket_key, cache)

            processed_this_run[test_name] = ticket_key
            _send_notification(bug_report, jira_result)
            results.append({
                "test":     test_name,
                "ticket":   ticket_key,
                "url":      jira_result.get("ticket_url", ""),
                "status":   status,
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

    # ── Final summary ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"📋 FINAL SUMMARY")
    print(f"{'='*60}")
    for r in results:
        icon = "✅" if r.get("status") in ("success", "duplicate", "skipped-same-run") else "❌"
        print(f"{icon} {r.get('test', 'unknown')[:60]}")
        print(f"   Ticket  : {r.get('ticket')} — {r.get('url', '')}")
        print(f"   Status  : {r.get('status')}")
        if r.get("severity"):
            print(f"   Severity: {r.get('severity')}")
    print(f"{'='*60}")

    # ── Clean up processed artifact files ─────────────────────────────
    print(f"\n🧹 Cleaning up {len(metadata_files)} processed artifact file(s)...")
    for metadata_path in metadata_files:
        try:
            os.remove(metadata_path)
        except Exception:
            pass


def _send_notification(bug_report: dict, jira_result: dict):
    from notifications.email_notifier import notify_email
    notify_email(bug_report, jira_result)


if __name__ == "__main__":
    asyncio.run(process_failures())