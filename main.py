"""
main.py
-------
Updated orchestrator with AI Fix Agent integration.
Modes:
  - normal: Run bug reporter only (backward compatible)
  - ai-fix-propose: Generate fixes, wait for human approval
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import glob
import json
import asyncio
from datetime import datetime, timedelta
from agent.failure_agent import FailureAnalysisAgent
from agent.mcp_client import JiraMCPClient

# AI Fix Agent
try:
    from agent.ai_fix_agent import AIFixAgent
    AI_FIX_AVAILABLE = True
except ImportError:
    AI_FIX_AVAILABLE = False

ARTIFACT_DIR = os.path.join(os.path.dirname(__file__), "artifacts")
CACHE_FILE = os.path.join(ARTIFACT_DIR, "dedup_cache.json")
TTL_HOURS = 24

# Redis (same as before)
try:
    import redis as redis_lib
    _redis = redis_lib.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
        socket_timeout=2,
    )
    _redis.ping()
    REDIS_OK = True
    print("✅ Redis connected")
except Exception as _e:
    REDIS_OK = False
    print(f"⚠️ Redis unavailable ({_e})")

# Cache functions (unchanged)
def _cache_key(test_name: str) -> str:
    safe = test_name.lower()
    for ch in " /\\.:[]()":
        safe = safe.replace(ch, "_")
    return safe[:200]

def _is_cached(test_name: str) -> bool:
    key = _cache_key(test_name)
    if REDIS_OK:
        try:
            if _redis.exists(f"dedup:{key}") == 1:
                print(f" 🔴 Redis: cache hit for {key[:50]}")
                return True
        except Exception as e:
            print(f" ⚠️ Redis read error: {e}")
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        entry = cache.get(key)
        if not entry:
            return False
        if datetime.now() - datetime.fromisoformat(entry["timestamp"]) > timedelta(hours=TTL_HOURS):
            return False
        print(f" 🟡 File cache: hit for {key[:50]}")
        return True
    except (FileNotFoundError, json.JSONDecodeError):
        return False

def _get_cached_ticket(test_name: str) -> str | None:
    key = _cache_key(test_name)
    if REDIS_OK:
        try:
            data = _redis.get(f"dedup:{key}")
            if data:
                return json.loads(data).get("ticket_key")
        except Exception:
            pass
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        entry = cache.get(key)
        if not entry:
            return None
        if datetime.now() - datetime.fromisoformat(entry["timestamp"]) > timedelta(hours=TTL_HOURS):
            return None
        return entry.get("ticket_key")
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def _mark_cached(test_name: str, ticket_key: str) -> None:
    key = _cache_key(test_name)
    data = json.dumps({
        "ticket_key": ticket_key,
        "timestamp": datetime.now().isoformat(),
        "test_name": test_name,
    })
    if REDIS_OK:
        try:
            _redis.set(f"dedup:{key}", data, ex=TTL_HOURS * 3600)
            print(f" 📌 Redis: cached {test_name[:50]} → {ticket_key}")
            return
        except Exception as e:
            print(f" ⚠️ Redis write error: {e}")
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    cache[key] = json.loads(data)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

# ── Main Pipeline ───────────────────────────────────────────────────

async def process_failures():
    enable_ai_fix = os.getenv("ENABLE_AI_FIX", "false").lower() == "true"
    fix_mode = os.getenv("FIX_MODE", "propose-only")
    
    pattern = os.path.join(ARTIFACT_DIR, "failure_*.json")
    metadata_files = sorted(glob.glob(pattern))

    if not metadata_files:
        print("✅ No test failures found. Nothing to report.")
        return

    print(f"\n{'='*60}")
    print(f"🚀 ZERO-EFFORT BUG REPORTER")
    print(f"{'='*60}")
    print(f"Failures: {len(metadata_files)}")
    print(f"AI Fix: {enable_ai_fix} (mode: {fix_mode})")

    agent = FailureAnalysisAgent()
    results = []
    processed_this_run = {}

    for i, metadata_path in enumerate(metadata_files, 1):
        print(f"\n[{i}/{len(metadata_files)}] {os.path.basename(metadata_path)}")

        try:
            agent_result = agent.analyze_failure(metadata_path)
            bug_report = agent_result["bug_report"]
            test_name = bug_report["metadata"]["test_name"]

            # Layer 1: Same-run dedup
            if test_name in processed_this_run:
                existing_key = processed_this_run[test_name]
                print(f"⚠️ [L1] Same-run duplicate → {existing_key}")
                results.append({
                    "test": test_name,
                    "ticket": existing_key,
                    "status": "skipped-same-run",
                })
                continue

            # Layer 2: Cache check
            if _is_cached(test_name):
                cached_key = _get_cached_ticket(test_name)
                print(f"⚠️ [L2] Cache hit → {cached_key}")
                print(f" Escalating + adding recurrence comment...")

                async with JiraMCPClient() as jira:
                    jira_result = await jira.create_jira_ticket(
                        bug_report=bug_report,
                        cached_ticket_key=cached_key,
                    )

                ticket_key = jira_result.get("ticket_key", cached_key or "N/A")
                processed_this_run[test_name] = ticket_key
                _send_notification(bug_report, jira_result)
                results.append({
                    "test": test_name,
                    "ticket": ticket_key,
                    "status": jira_result.get("status", "duplicate"),
                })
                continue

            # Layer 3: Create new Jira ticket
            async with JiraMCPClient() as jira:
                jira_result = await jira.create_jira_ticket(bug_report)

            ticket_key = jira_result.get("ticket_key", "N/A")
            status = jira_result.get("status", "unknown")

            if status in ("success", "duplicate") and ticket_key not in ("N/A", "ERROR"):
                _mark_cached(test_name, ticket_key)

            processed_this_run[test_name] = ticket_key
            _send_notification(bug_report, jira_result)
            
            results.append({
                "test": test_name,
                "ticket": ticket_key,
                "status": status,
                "severity": bug_report["severity"],
            })

        except Exception as e:
            print(f"❌ Failed: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "test": metadata_path,
                "ticket": "ERROR",
                "status": str(e),
            })

    # Summary
    print(f"\n{'='*60}")
    print(f"📋 SUMMARY")
    print(f"{'='*60}")
    for r in results:
        icon = "✅" if r.get("status") in ("success", "duplicate", "skipped-same-run") else "❌"
        print(f"{icon} {r.get('test', 'unknown')[:60]}")
        print(f"   Ticket: {r.get('ticket')} | Status: {r.get('status')}")
    print(f"{'='*60}")

    # Cleanup
    print(f"\n🧹 Cleaning up {len(metadata_files)} artifacts...")
    for mf in metadata_files:
        try:
            os.remove(mf)
        except Exception:
            pass

def _send_notification(bug_report: dict, jira_result: dict):
    from notifications.email_notifier import notify_email
    notify_email(bug_report, jira_result)

if __name__ == "__main__":
    asyncio.run(process_failures())