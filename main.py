"""
main.py
--------
Main orchestrator — finds all failure artifacts and processes them.

Dedup layers:
  1. Same-run dict       — catches duplicates within one run
  2. Redis cache         — catches duplicates across runs (24hr TTL)
                           Falls back to file cache if Redis is down
  3. Jira label search   — final check via mcp_client
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

# ── Redis connection ───────────────────────────────────────────────────

try:
    import redis as redis_lib
    _redis = redis_lib.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses = True,
        socket_timeout   = 2,
    )
    _redis.ping()
    REDIS_OK = True
    print("✅ Redis connected")
except Exception as _e:
    REDIS_OK = False
    print(f"⚠️  Redis unavailable ({_e}) — falling back to file cache")


# ── Cache key ──────────────────────────────────────────────────────────

def _cache_key(test_name: str) -> str:
    safe = test_name.lower()
    for ch in " /\\.[]():":
        safe = safe.replace(ch, "_")
    return safe[:200]


# ── Cache read ─────────────────────────────────────────────────────────

def _is_cached(test_name: str) -> bool:
    key = _cache_key(test_name)

    # ── Redis ─────────────────────────────────────────────────────────
    if REDIS_OK:
        try:
            exists = _redis.exists(f"dedup:{key}") == 1
            if exists:
                print(f"   🔴 Redis: cache hit for {key[:50]}")
            return exists
        except Exception as e:
            print(f"   ⚠️  Redis read error: {e} — checking file cache")

    # ── File fallback ─────────────────────────────────────────────────
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        entry = cache.get(key)
        if not entry:
            return False
        if datetime.now() - datetime.fromisoformat(entry["timestamp"]) > timedelta(hours=TTL_HOURS):
            return False
        print(f"   🟡 File cache: hit for {key[:50]}")
        return True
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def _get_cached_ticket(test_name: str) -> str | None:
    key = _cache_key(test_name)

    # ── Redis ─────────────────────────────────────────────────────────
    if REDIS_OK:
        try:
            data = _redis.get(f"dedup:{key}")
            if data:
                return json.loads(data).get("ticket_key")
        except Exception:
            pass

    # ── File fallback ─────────────────────────────────────────────────
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


# ── Cache write ────────────────────────────────────────────────────────

def _mark_cached(test_name: str, ticket_key: str) -> None:
    key  = _cache_key(test_name)
    data = json.dumps({
        "ticket_key": ticket_key,
        "timestamp":  datetime.now().isoformat(),
        "test_name":  test_name,
    })

    # ── Redis ─────────────────────────────────────────────────────────
    if REDIS_OK:
        try:
            _redis.set(f"dedup:{key}", data, ex=TTL_HOURS * 3600)
            print(f"   📌 Redis: cached {test_name[:50]} → {ticket_key} (TTL {TTL_HOURS}h)")
            _record_analytics(test_name, ticket_key)
            return
        except Exception as e:
            print(f"   ⚠️  Redis write error: {e} — using file cache")

    # ── File fallback ─────────────────────────────────────────────────
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    cache[key] = json.loads(data)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    print(f"   💾 File cache: stored {test_name[:50]} → {ticket_key}")


# ── Analytics (Redis only) ─────────────────────────────────────────────

def _record_analytics(test_name: str, ticket_key: str) -> None:
    if not REDIS_OK:
        return
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        _redis.hincrby(f"analytics:tickets:{today}", ticket_key, 1)
        _redis.hincrby(f"analytics:tests:{today}", _cache_key(test_name), 1)
        _redis.expire(f"analytics:tickets:{today}", 30 * 86400)
        _redis.expire(f"analytics:tests:{today}",  30 * 86400)
    except Exception:
        pass


def _print_analytics() -> None:
    if not REDIS_OK:
        return
    try:
        today   = datetime.now().strftime("%Y-%m-%d")
        tickets = _redis.hgetall(f"analytics:tickets:{today}") or {}
        tests   = _redis.hgetall(f"analytics:tests:{today}")   or {}
        if tickets or tests:
            print(f"\n📊 TODAY'S ANALYTICS ({today})")
            print(f"{'='*60}")
            print(f"   Tickets reported  : {dict(tickets)}")
            print(f"   Unique test runs  : {len(tests)}")
            print(f"{'='*60}")
    except Exception:
        pass


# ── Rate limiting (Redis only) ─────────────────────────────────────────

def _check_rate_limit(limit: int = 20) -> bool:
    """Returns True if under limit. False if rate limit exceeded."""
    if not REDIS_OK:
        return True
    try:
        key   = "rate_limit:jira_tickets"
        count = _redis.get(key)
        if count is None:
            _redis.set(key, 1, ex=3600)
            return True
        count = int(count)
        if count >= limit:
            print(f"   ⚠️  Rate limit: {count}/{limit} tickets this hour — skipping")
            return False
        _redis.incr(key)
        print(f"   📊 Rate limit: {count + 1}/{limit} this hour")
        return True
    except Exception:
        return True


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
    processed_this_run = {}

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

            # ── Layer 2: Redis/file cache ─────────────────────────────
            # NOTE: In GitHub Actions, Redis resets every run so cache
            # is always empty. We skip to Layer 3 (Jira search) which
            # persists across runs via ticket labels.
            if _is_cached(test_name):
                cached_key = _get_cached_ticket(test_name)
                print(f"⚠️  [Layer 2] Cache hit — already reported as {cached_key}")
                print(f"   Escalating priority + adding recurrence comment...")

                async with JiraMCPClient() as jira:
                    jira_result = await jira.create_jira_ticket(
                        bug_report        = bug_report,
                        cached_ticket_key = cached_key,
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

            # ── Layer 3: Jira API ─────────────────────────────────────
            async with JiraMCPClient() as jira:
                jira_result = await jira.create_jira_ticket(bug_report)

            ticket_key = jira_result.get("ticket_key", "N/A")
            status     = jira_result.get("status", "unknown")

            if status in ("success", "duplicate") and ticket_key not in ("N/A", "ERROR"):
                _mark_cached(test_name, ticket_key)

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

    _print_analytics()

    # ── Clean up processed artifacts ───────────────────────────────────
    print(f"\n🧹 Cleaning up {len(metadata_files)} artifact file(s)...")
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