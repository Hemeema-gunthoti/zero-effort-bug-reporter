#!/usr/bin/env python3
"""
test_setup.py
-------------
Quick validation script to check your environment before running the pipeline.
"""

import os
import sys
import glob

def check_env():
    """Check all required environment variables."""
    print("="*60)
    print("🔍 ENVIRONMENT VALIDATION")
    print("="*60)
    
    required = {
        "JIRA_BASE_URL": "Jira instance URL",
        "JIRA_EMAIL": "Jira account email",
        "JIRA_API_TOKEN": "Jira API token",
        "JIRA_PROJECT_KEY": "Jira project key",
        "GROQ_API_KEY": "Groq API key for AI analysis",
    }
    
    optional = {
        "SMTP_USERNAME": "Gmail username for notifications",
        "SMTP_PASSWORD": "Gmail app password",
        "JIRA_SEVERITY_FIELD": "Custom severity field ID",
        "GROQ_MODEL": "Model name (default: llama-3.1-8b-instant)",
        "ENABLE_AI_FIX": "Set to 'true' to enable AI fix agent",
        "FIX_MODE": "Set to 'propose-only' for human approval",
    }
    
    all_good = True
    
    print("\n📋 REQUIRED:")
    for var, desc in required.items():
        val = os.getenv(var)
        if val:
            print(f"  ✅ {var}: SET")
        else:
            print(f"  ❌ {var}: NOT SET — {desc}")
            all_good = False
    
    print("\n📋 OPTIONAL:")
    for var, desc in optional.items():
        val = os.getenv(var)
        if val:
            print(f"  ✅ {var}: SET ({val[:20]}...)")
        else:
            print(f"  ⚪ {var}: NOT SET — {desc}")
    
    return all_good

def check_artifacts():
    """Check for test failure artifacts."""
    print("\n📁 ARTIFACTS:")
    
    artifact_dir = "artifacts"
    if not os.path.exists(artifact_dir):
        print(f"  ⚠️  {artifact_dir}/ directory not found")
        return []
    
    failure_files = sorted(glob.glob(os.path.join(artifact_dir, "failure_*.json")))
    print(f"  Found {len(failure_files)} failure artifact(s)")
    
    for f in failure_files:
        print(f"    - {os.path.basename(f)}")
    
    return failure_files

def main():
    print("\n" + "="*60)
    print("🚀 ZERO-EFFORT BUG REPORTER — SETUP VALIDATOR")
    print("="*60)
    
    env_ok = check_env()
    failures = check_artifacts()
    
    print("\n" + "="*60)
    print("📊 SUMMARY")
    print("="*60)
    
    if not env_ok:
        print("❌ REQUIRED variables missing — fix before running")
        sys.exit(1)
    
    if not failures:
        print("⚠️  No test failures found — pipeline will exit immediately")
        print("   (This is normal if tests passed)")
    else:
        print(f"✅ {len(failures)} failure(s) ready to process")
    
    print("\n✅ Setup validation complete")
    print("   Run: python main.py")

if __name__ == "__main__":
    main()