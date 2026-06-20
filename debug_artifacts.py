#!/usr/bin/env python3
"""
debug_artifacts.py
------------------
Quick script to inspect what's in your artifacts directory.
"""

import os
import json
import glob

ARTIFACT_DIR = "artifacts"

print("="*60)
print("🔍 ARTIFACT DEBUG")
print("="*60)

if not os.path.exists(ARTIFACT_DIR):
    print(f"\n❌ {ARTIFACT_DIR}/ does not exist!")
    print("   Tests may not have run, or artifacts were cleaned up.")
    exit(1)

print(f"\n📁 Contents of {ARTIFACT_DIR}/:")
for item in sorted(os.listdir(ARTIFACT_DIR)):
    path = os.path.join(ARTIFACT_DIR, item)
    if os.path.isfile(path):
        size = os.path.getsize(path)
        print(f"  📄 {item} ({size} bytes)")
    else:
        print(f"  📂 {item}/")
        for subitem in sorted(os.listdir(path)):
            subpath = os.path.join(path, subitem)
            size = os.path.getsize(subpath) if os.path.isfile(subpath) else "dir"
            print(f"     - {subitem} ({size} bytes)")

# Check failure artifacts
print(f"\n📋 Failure Artifacts:")
failure_files = sorted(glob.glob(os.path.join(ARTIFACT_DIR, "failure_*.json")))
if not failure_files:
    print("  ❌ No failure_*.json files found!")
    print("  This means either:")
    print("    1. Tests passed — no failures to report")
    print("    2. Tests failed but pytest didn't generate artifacts")
    print("    3. Artifacts were already cleaned up by a previous run")
else:
    for f in failure_files:
        print(f"\n  📄 {os.path.basename(f)}")
        try:
            with open(f) as fh:
                data = json.load(fh)
            for key, val in data.items():
                if isinstance(val, str) and len(val) > 100:
                    print(f"    {key}: {val[:100]}...")
                else:
                    print(f"    {key}: {val}")
        except Exception as e:
            print(f"    ❌ Error reading: {e}")

# Check test output
print(f"\n📋 Test Output:")
test_output = os.path.join(ARTIFACT_DIR, "test_output.txt")
if os.path.exists(test_output):
    with open(test_output) as f:
        lines = f.readlines()
    print(f"  Total lines: {len(lines)}")
    # Show last 20 lines
    print("  Last 20 lines:")
    for line in lines[-20:]:
        print(f"    {line.rstrip()}")
else:
    print("  ❌ test_output.txt not found")

print("\n" + "="*60)