"""
main.py
-------
Updated orchestrator with:
- Redis REMOVED (file-based dedup cache only)
- Better error logging at every step
- AI Fix Agent integration
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
    print("✅ AI Fix Agent loaded")
except ImportError as e:
    AI_FIX_AVAILABLE = False
    print(f"⚠️ AI Fix Agent not available: {e}")

ARTIFACT_DIR = os.path.join(os.path.dirname(__file__), "artifacts")
CACHE_FILE = os.path.join(ARTIFACT_DIR, "dedup_cache.json")
TTL_HOURS = 24

print(f"📁 Artifact directory: {ARTIFACT_DIR}")
print(f"📁 Cache file: {CACHE_FILE}")

# ═══════════════════════════════════════════════════════════════════
# FILE-BASED DEDUP CACHE ONLY (Redis removed)
# ═══════════════════════════════════════════════════════════════════

def _cache_key(test_name: str) -> str:
    safe = test_name.lower()
    for ch in " /\\.:[]()":
        safe = safe.replace(ch, "_")
    return safe[:200]

def _is_cached(test_name: str) -> bool:
    key = _cache_key(test_name)
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        entry = cache.get(key)
        if not entry:
            print(f"  🟢 Cache miss: {key[:50]}")
            return False
        if datetime.now() - datetime.fromisoformat(entry["timestamp"]) > timedelta(hours=TTL_HOURS):
            print(f"  🟡 Cache expired: {key[:50]}")
            return False
        print(f"  🟡 Cache hit: {key[:50]} → {entry.get('ticket_key', 'N/A')}")
        return True
    except FileNotFoundError:
        print(f"  🟢 No cache file yet")
        return False
    except json.JSONDecodeError as e:
        print(f"  ⚠️ Cache file corrupt: {e}")
        return False

def _get_cached_ticket(test_name: str) -> str | None:
    key = _cache_key(test_name)
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
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    
    cache[key] = {
        "ticket_key": ticket_key,
        "timestamp": datetime.now().isoformat(),
        "test_name": test_name,
    }
    
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    
    print(f"  📌 Cached: {test_name[:50]} → {ticket_key}")

# ═══════════════════════════════════════════════════════════════════
# DEBUG: Check environment
# ═══════════════════════════════════════════════════════════════════

def _debug_env():
    """Print all environment variables for debugging."""
    print("\n" + "="*60)
    print("🔍 ENVIRONMENT DEBUG")
    print("="*60)
    
    env_vars = [
        "JIRA_BASE_URL",
        "JIRA_EMAIL", 
        "JIRA_API_TOKEN",
        "JIRA_PROJECT_KEY",
        "JIRA_SEVERITY_FIELD",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_SERVER",
        "SMTP_PORT",
        "FROM_EMAIL",
        "APP_URL",
        "GITHUB_ACTOR",
        "GIT_AUTHOR_EMAIL",
        "ENABLE_AI_FIX",
        "FIX_MODE",
    ]
    
    for var in env_vars:
        val = os.getenv(var)
        status = "✅ SET" if val else "❌ NOT SET"
        # Mask sensitive values
        if val and any(s in var.lower() for s in ["token", "password", "key"]):
            display = val[:4] + "****" + val[-4:] if len(val) > 8 else "****"
        else:
            display = val or "N/A"
        print(f"  {var}: {status} ({display})")
    
    # Check for test artifacts
    failure_files = sorted(glob.glob(os.path.join(ARTIFACT_DIR, "failure_*.json")))
    print(f"\n  📁 Failure artifacts found: {len(failure_files)}")
    for f in failure_files:
        print(f"    - {os.path.basename(f)}")
    
    print("="*60 + "\n")

# ═══════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════

async def process_failures():
    """Process test failures: create Jira tickets, optionally propose AI fixes."""
    
    # Debug environment first
    _debug_env()
    
    enable_ai_fix = os.getenv("ENABLE_AI_FIX", "false").lower() == "true"
    fix_mode = os.getenv("FIX_MODE", "propose-only")
    
    print(f"\n{'='*60}")
    print(f"🚀 ZERO-EFFORT BUG REPORTER STARTING")
    print(f"{'='*60}")
    print(f"  AI Fix enabled: {enable_ai_fix}")
    print(f"  Fix mode: {fix_mode}")
    
    pattern = os.path.join(ARTIFACT_DIR, "failure_*.json")
    metadata_files = sorted(glob.glob(pattern))

    if not metadata_files:
        print("\n✅ No test failures found. Nothing to report.")
        print("   (No failure_*.json files in artifacts/)")
        return

    print(f"\n📊 Found {len(metadata_files)} failure(s) to process")

    agent = FailureAnalysisAgent()
    results = []
    processed_this_run = {}

    for i, metadata_path in enumerate(metadata_files, 1):
        print(f"\n[{i}/{len(metadata_files)}] Processing: {os.path.basename(metadata_path)}")

        try:
            # Step 1: Analyze failure
            print("  🔍 Analyzing failure...")
            agent_result = agent.analyze_failure(metadata_path)
            
            if not agent_result or "bug_report" not in agent_result:
                print(f"  ⚠️ Failed to generate bug report")
                results.append({
                    "test": metadata_path,
                    "ticket": "ERROR",
                    "status": "analysis_failed",
                })
                continue
            
            bug_report = agent_result["bug_report"]
            test_name = bug_report["metadata"]["test_name"]
            print(f"  📝 Test: {test_name}")
            print(f"  🔴 Severity: {bug_report.get('severity', 'unknown')}")

            # Layer 1: Same-run dedup
            if test_name in processed_this_run:
                existing_key = processed_this_run[test_name]
                print(f"  ⚠️ [L1] Same-run duplicate — already processed as {existing_key}")
                results.append({
                    "test": test_name,
                    "ticket": existing_key,
                    "status": "skipped-same-run",
                })
                continue

            # Layer 2: Cache check
            if _is_cached(test_name):
                cached_key = _get_cached_ticket(test_name)
                if cached_key:
                    print(f"  ⚠️ [L2] Cache hit — already reported as {cached_key}")
                    print(f"  📈 Escalating priority + adding recurrence comment...")

                    try:
                        async with JiraMCPClient() as jira:
                            jira_result = await jira.create_jira_ticket(
                                bug_report=bug_report,
                                cached_ticket_key=cached_key,
                            )
                        
                        ticket_key = jira_result.get("ticket_key", cached_key)
                        status = jira_result.get("status", "duplicate")
                        print(f"  ✅ Jira updated: {ticket_key} ({status})")
                    except Exception as e:
                        print(f"  ❌ Jira API error: {e}")
                        ticket_key = cached_key
                        status = "jira_error"

                    processed_this_run[test_name] = ticket_key
                    _send_notification(bug_report, {"ticket_key": ticket_key, "status": status})
                    results.append({
                        "test": test_name,
                        "ticket": ticket_key,
                        "status": status,
                    })
                    continue

            # Layer 3: Create new Jira ticket
            print(f"  🎫 Creating new Jira ticket...")
            try:
                async with JiraMCPClient() as jira:
                    jira_result = await jira.create_jira_ticket(bug_report)
                
                ticket_key = jira_result.get("ticket_key", "N/A")
                status = jira_result.get("status", "unknown")
                
                print(f"  ✅ Jira ticket created: {ticket_key} ({status})")

                if status in ("success", "duplicate") and ticket_key not in ("N/A", "ERROR"):
                    _mark_cached(test_name, ticket_key)
                else:
                    print(f"  ⚠️ Unexpected Jira status: {status}")

            except Exception as e:
                print(f"  ❌ Failed to create Jira ticket: {e}")
                import traceback
                traceback.print_exc()
                ticket_key = "ERROR"
                status = f"exception: {str(e)[:100]}"

            processed_this_run[test_name] = ticket_key
            _send_notification(bug_report, {"ticket_key": ticket_key, "status": status})
            
            results.append({
                "test": test_name,
                "ticket": ticket_key,
                "status": status,
                "severity": bug_report.get("severity", "unknown"),
            })

        except Exception as e:
            print(f"  ❌ Failed to process {os.path.basename(metadata_path)}: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "test": metadata_path,
                "ticket": "ERROR",
                "status": f"exception: {str(e)[:100]}",
            })

    # Final summary
    print(f"\n{'='*60}")
    print(f"📋 FINAL SUMMARY")
    print(f"{'='*60}")
    
    success_count = sum(1 for r in results if r.get("status") in ("success", "duplicate", "skipped-same-run"))
    fail_count = len(results) - success_count
    
    print(f"  Total: {len(results)} | ✅ Success: {success_count} | ❌ Failed: {fail_count}")
    print()
    
    for r in results:
        icon = "✅" if r.get("status") in ("success", "duplicate", "skipped-same-run") else "❌"
        print(f"  {icon} {r.get('test', 'unknown')[:60]}")
        print(f"     Ticket: {r.get('ticket')} | Status: {r.get('status')}")
    print(f"{'='*60}")

    # Cleanup
    print(f"\n🧹 Cleaning up {len(metadata_files)} artifact file(s)...")
    for metadata_path in metadata_files:
        try:
            os.remove(metadata_path)
            print(f"  🗑️  Removed: {os.path.basename(metadata_path)}")
        except Exception as e:
            print(f"  ⚠️ Failed to remove {metadata_path}: {e}")
    
    print("\n✅ Pipeline complete")

def _send_notification(bug_report: dict, jira_result: dict):
    """Send email notification."""
    try:
        from notifications.email_notifier import notify_email
        notify_email(bug_report, jira_result)
        print("  📧 Email notification sent")
    except Exception as e:
        print(f"  ⚠️ Email notification failed: {e}")

if __name__ == "__main__":
    asyncio.run(process_failures())