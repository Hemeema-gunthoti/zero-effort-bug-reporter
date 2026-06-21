"""
main.py
-------
Fixed version with:
- Redis removed
- Detailed logging at every step
- Jira ticket creation validation
- Proper artifact reading
- FIX: AI Fix Agent runs BEFORE cleanup
- FIX: Export JIRA_TICKET for workflow step
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import glob
import json
import asyncio
from datetime import datetime, timedelta

print("=" * 60)
print("🚀 ZERO-EFFORT BUG REPORTER STARTING")
print("=" * 60)

# Check imports
print("\n📦 Loading modules...")
try:
    from agent.failure_agent import FailureAnalysisAgent
    print(" ✅ FailureAnalysisAgent")
except Exception as e:
    print(f" ❌ FailureAnalysisAgent: {e}")
    sys.exit(1)

try:
    from agent.mcp_client import JiraMCPClient
    print(" ✅ JiraMCPClient")
except Exception as e:
    print(f" ❌ JiraMCPClient: {e}")
    sys.exit(1)

try:
    from agent.ai_fix_agent import AIFixAgent
    AI_FIX_AVAILABLE = True
    print(" ✅ AIFixAgent")
except Exception as e:
    AI_FIX_AVAILABLE = False
    print(f" ⚠️ AIFixAgent: {e}")

ARTIFACT_DIR = os.path.join(os.path.dirname(__file__), "artifacts")
CACHE_FILE = os.path.join(ARTIFACT_DIR, "dedup_cache.json")
TTL_HOURS = 24

print(f"\n📁 Artifact directory: {ARTIFACT_DIR}")
print(f"📁 Cache file: {CACHE_FILE}")

# ═══════════════════════════════════════════════════════════════════
# FILE-BASED DEDUP CACHE
# ═══════════════════════════════════════════════════════════════════

def _cache_key(test_name: str, error_type: str = None) -> str:
    safe = test_name.lower()
    for ch in " /\\\\\\.:[]()":
        safe = safe.replace(ch, "_")
    if error_type:
        error_safe = error_type.lower().replace(" ", "_")
        safe = f"{safe}__{error_safe}"
    return safe[:200]

def _is_cached(test_name: str, error_type: str = None) -> bool:
    key = _cache_key(test_name, error_type)
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        entry = cache.get(key)
        if not entry:
            return False
        if datetime.now() - datetime.fromisoformat(entry["timestamp"]) > timedelta(hours=TTL_HOURS):
            print(f" 🟡 Cache expired: {key[:50]}")
            return False
        print(f" 🟡 Cache hit: {key[:50]} → {entry.get('ticket_key', 'N/A')}")
        return True
    except FileNotFoundError:
        return False
    except json.JSONDecodeError:
        return False

def _get_cached_ticket(test_name: str, error_type: str = None) -> str | None:
    key = _cache_key(test_name, error_type)
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

def _mark_cached(test_name: str, ticket_key: str, error_type: str = None) -> None:
    key = _cache_key(test_name, error_type)
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
        "error_type": error_type,
    }

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

# ═══════════════════════════════════════════════════════════════════
# DEBUG ENVIRONMENT
# ═══════════════════════════════════════════════════════════════════

def _debug_env():
    print("\n" + "=" * 60)
    print("🔍 ENVIRONMENT CHECK")
    print("=" * 60)

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
        status = "✅" if val else "❌"
        if val and any(s in var.lower() for s in ["token", "password", "key"]):
            display = val[:4] + "****" + val[-4:] if len(val) > 8 else "****"
        else:
            display = val or "NOT SET"
        print(f" {status} {var}: {display}")

    # Check artifacts
    print(f"\n📁 Checking artifacts in: {ARTIFACT_DIR}")
    if os.path.exists(ARTIFACT_DIR):
        all_files = os.listdir(ARTIFACT_DIR)
        print(f" Total files: {len(all_files)}")

        failure_files = sorted(glob.glob(os.path.join(ARTIFACT_DIR, "failure_*.json")))
        print(f" Failure artifacts: {len(failure_files)}")
        for f in failure_files:
            print(f"   - {os.path.basename(f)}")

        bug_reports = sorted(glob.glob(os.path.join(ARTIFACT_DIR, "bug_reports", "*.json")))
        print(f" Bug reports: {len(bug_reports)}")
        for f in bug_reports:
            print(f"   - {os.path.basename(f)}")
    else:
        print(f" ❌ Directory does not exist!")

    print("=" * 60)

# ═══════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════

async def process_failures():
    _debug_env()

    enable_ai_fix = os.getenv("ENABLE_AI_FIX", "false").lower() == "true"
    fix_mode = os.getenv("FIX_MODE", "propose-only")

    print(f"\n{'=' * 60}")
    print(f"⚙️  CONFIGURATION")
    print(f"{'=' * 60}")
    print(f" AI Fix enabled: {enable_ai_fix}")
    print(f" Fix mode: {fix_mode}")

    # Find failure artifacts
    pattern = os.path.join(ARTIFACT_DIR, "failure_*.json")
    metadata_files = sorted(glob.glob(pattern))

    print(f"\n📊 Found {len(metadata_files)} failure artifact(s)")

    if not metadata_files:
        print("\n✅ No test failures to process.")
        print(" (No failure_*.json files found in artifacts/)")
        print("\n This means either:")
        print(" 1. All tests passed — nothing to report")
        print(" 2. Tests failed but artifacts weren't generated")
        print(" 3. Artifacts were already cleaned up")
        return

    agent = FailureAnalysisAgent()
    results = []
    processed_this_run = {}
    all_ticket_keys = []

    for i, metadata_path in enumerate(metadata_files, 1):
        print(f"\n{'=' * 60}")
        print(f"[{i}/{len(metadata_files)}] Processing: {os.path.basename(metadata_path)}")
        print(f"{'=' * 60}")

        try:
            print("\n📄 Artifact content:")
            with open(metadata_path, "r") as f:
                artifact_data = json.load(f)
            print(f"   Test: {artifact_data.get('test_name', 'N/A')}")
            print(f"   File: {artifact_data.get('file', 'N/A')}")
            print(f"   Summary: {artifact_data.get('failure_summary', 'N/A')[:100]}...")

            print("\n🔍 Running FailureAnalysisAgent...")
            agent_result = agent.analyze_failure(metadata_path)

            if not agent_result:
                print(" ❌ analyze_failure() returned None")
                results.append({"test": metadata_path, "ticket": "ERROR", "status": "null_result"})
                continue

            if "bug_report" not in agent_result:
                print(f" ❌ No 'bug_report' in result. Keys: {list(agent_result.keys())}")
                results.append({"test": metadata_path, "ticket": "ERROR", "status": "no_bug_report"})
                continue

            bug_report = agent_result["bug_report"]
            test_name = bug_report.get("metadata", {}).get("test_name", "unknown")
            error_type = bug_report.get("metadata", {}).get("error_type")

            print(f"\n📝 Bug Report Generated:")
            print(f"   Test: {test_name}")
            print(f"   Severity: {bug_report.get('severity', 'unknown')}")
            print(f"   Title: {bug_report.get('title', 'N/A')[:80]}...")

            dedup_key = (test_name, error_type)
            if dedup_key in processed_this_run:
                existing_key = processed_this_run[dedup_key]
                print(f"\n⚠️ [L1] Same-run duplicate → {existing_key}")
                results.append({"test": test_name, "ticket": existing_key, "status": "skipped-same-run"})
                continue

            if _is_cached(test_name, error_type):
                cached_key = _get_cached_ticket(test_name, error_type)
                if cached_key:
                    print(f"\n⚠️ [L2] Cache hit → {cached_key}")
                    print(f"📈 Escalating priority...")

                    try:
                        async with JiraMCPClient() as jira:
                            jira_result = await jira.create_jira_ticket(
                                bug_report=bug_report,
                                cached_ticket_key=cached_key,
                            )

                        ticket_key = jira_result.get("ticket_key", cached_key)
                        status = jira_result.get("status", "duplicate")
                        print(f"✅ Jira updated: {ticket_key} ({status})")
                    except Exception as e:
                        print(f"❌ Jira API error: {e}")
                        ticket_key = cached_key
                        status = "jira_error"

                    processed_this_run[dedup_key] = ticket_key
                    all_ticket_keys.append(ticket_key)
                    results.append({"test": test_name, "ticket": ticket_key, "status": status})
                    continue

            print(f"\n🎫 Creating NEW Jira ticket...")
            print(f"   Project: {os.getenv('JIRA_PROJECT_KEY', 'NOT SET')}")
            print(f"   Base URL: {os.getenv('JIRA_BASE_URL', 'NOT SET')}")

            try:
                async with JiraMCPClient() as jira:
                    jira_result = await jira.create_jira_ticket(bug_report)

                ticket_key = jira_result.get("ticket_key")
                status = jira_result.get("status", "unknown")

                print(f"\n📋 Jira Response:")
                print(f"   Ticket Key: {ticket_key}")
                print(f"   Status: {status}")
                print(f"   URL: {jira_result.get('ticket_url', 'N/A')}")

                if ticket_key and ticket_key not in ("N/A", "ERROR", None):
                    print(f"✅ Ticket created successfully: {ticket_key}")
                    _mark_cached(test_name, ticket_key, error_type)
                    all_ticket_keys.append(ticket_key)
                else:
                    print(f"❌ Ticket creation failed — key is: {ticket_key}")
                    print(f"   Full response: {json.dumps(jira_result, indent=2)}")

            except Exception as e:
                print(f"\n❌ EXCEPTION during Jira creation:")
                print(f"   Error: {e}")
                import traceback
                traceback.print_exc()
                ticket_key = "ERROR"
                status = f"exception: {str(e)[:100]}"

            processed_this_run[dedup_key] = ticket_key
            results.append({
                "test": test_name,
                "ticket": ticket_key,
                "status": status,
                "severity": bug_report.get("severity", "unknown"),
            })

        except Exception as e:
            print(f"\n❌ FAILED to process {os.path.basename(metadata_path)}:")
            print(f"   Error: {e}")
            import traceback
            traceback.print_exc()
            results.append({"test": metadata_path, "ticket": "ERROR", "status": f"exception: {str(e)[:100]}"})

    # Final summary
    print(f"\n{'=' * 60}")
    print(f"📋 FINAL SUMMARY")
    print(f"{'=' * 60}")

    success_count = sum(1 for r in results if r.get("status") in ("success", "duplicate", "skipped-same-run"))
    fail_count = len(results) - success_count

    print(f"\n   Total: {len(results)} | ✅ Success: {success_count} | ❌ Failed: {fail_count}")
    print()

    for r in results:
        icon = "✅" if r.get("status") in ("success", "duplicate", "skipped-same-run") else "❌"
        print(f"   {icon} {r.get('test', 'unknown')[:60]}")
        print(f"      Ticket: {r.get('ticket')} | Status: {r.get('status')}")

    print(f"\n{'=' * 60}")

    # Export ALL ticket keys to GITHUB_ENV before cleanup
    unique_keys = list(dict.fromkeys(k for k in all_ticket_keys if k and k not in ("ERROR", "N/A", None)))
    if unique_keys:
        primary_key = unique_keys[0]
        print(f"\n🔑 Exporting JIRA_TICKET={primary_key}")
        github_env = os.getenv("GITHUB_ENV")
        if github_env:
            with open(github_env, "a") as f:
                f.write(f"JIRA_TICKET={primary_key}\n")
                f.write(f"JIRA_TICKETS={','.join(unique_keys)}\n")
            print(f"   Written to GITHUB_ENV")
        os.environ["JIRA_TICKET"] = primary_key
        os.environ["JIRA_TICKETS"] = ",".join(unique_keys)
    else:
        print(f"\n⚠️ No valid ticket keys to export")

    # Run AI Fix Agent BEFORE cleanup — use propose_fix_batch so ALL bugs
    # are fixed in one LLM call instead of one call per report (which caused
    # the second call to overwrite the first's app/main.py in the manifest).
    if enable_ai_fix and AI_FIX_AVAILABLE:
        print(f"\n{'=' * 60}")
        print(f"🤖 AI FIX AGENT")
        print(f" Mode: {fix_mode}")
        print(f"{'=' * 60}")
        try:
            fix_agent = AIFixAgent()
            bug_report_dir = os.path.join(ARTIFACT_DIR, "bug_reports")
            if os.path.exists(bug_report_dir):
                bug_report_files = sorted(glob.glob(os.path.join(bug_report_dir, "bug_report_*.json")))
                if bug_report_files:
                    print(f"\n🔧 Processing {len(bug_report_files)} bug report(s) as single batch")
                    fix_result = fix_agent.propose_fix_batch(bug_report_files, mode=fix_mode)
                    print(f"   Result: bugs_addressed={fix_result.get('bugs_addressed', '?')}, fixed={fix_result.get('fixed')}")
                else:
                    print("   No bug reports found for AI fix")
            else:
                print("   No bug reports directory found")
        except Exception as e:
            print(f"   ❌ AI Fix Agent error: {e}")
            import traceback
            traceback.print_exc()

    # Cleanup AFTER AI Fix Agent
    print(f"\n🧹 Cleaning up {len(metadata_files)} artifact(s)...")
    for metadata_path in metadata_files:
        try:
            os.remove(metadata_path)
            print(f"   🗑️  {os.path.basename(metadata_path)}")
        except Exception as e:
            print(f"   ⚠️ Failed to remove: {e}")

    print("\n✅ Pipeline complete")

    return results


if __name__ == "__main__":
    asyncio.run(process_failures())