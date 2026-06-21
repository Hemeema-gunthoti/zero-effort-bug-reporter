"""
ai_fix_agent.py
Reads pytest failure results, sends them to Groq, and applies patches to source files.

Usage:
  python agent/ai_fix_agent.py \
    --test-results path/to/test_results.json \
    --repo-path .
"""

import argparse
import json
import os
import sys
import re
from pathlib import Path

try:
    from groq import Groq
except ImportError:
    print("[ai_fix_agent] groq not installed. Run: pip install groq")
    sys.exit(1)


# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL   = os.environ.get("GROQ_MODEL", "llama3-8b-8192")
MAX_RETRIES  = 3


def load_failures(results_path: str) -> list[dict]:
    """Load failed test details from pytest-json-report output."""
    with open(results_path) as f:
        data = json.load(f)
    failures = []
    for t in data.get("tests", []):
        if t.get("outcome") == "failed":
            failures.append({
                "nodeid":  t["nodeid"],
                "longrepr": t.get("call", {}).get("longrepr", ""),
                "stdout":   t.get("call", {}).get("stdout", ""),
            })
    return failures


def _read_source_file(filepath: str, repo_path: str) -> str | None:
    """Try to read the source file implicated in the test node id."""
    full = Path(repo_path) / filepath
    if full.exists():
        return full.read_text(encoding="utf-8")
    return None


def _extract_source_path_from_nodeid(nodeid: str) -> str:
    """
    pytest nodeids look like: tests/test_foo.py::TestClass::test_method
    Guess the source module from the test name heuristic.
    """
    parts = nodeid.split("::")
    test_file = parts[0]
    # Strip tests/ prefix and test_ prefix to guess source module
    source_guess = test_file.replace("tests/", "").replace("test_", "")
    return source_guess


def build_groq_prompt(failure: dict, source_code: str | None) -> str:
    source_block = f"""
<source_file>
{source_code[:3000] if source_code else 'Could not load source file.'}
</source_file>
""" if source_code else ""

    return f"""You are an expert Python software engineer and QA specialist.
A test is failing. Your job is to:
1. Analyze the failure
2. Identify the root cause
3. Generate a minimal, targeted fix

<failing_test>
{failure['nodeid']}
</failing_test>

<error_traceback>
{failure['longrepr'][:2000]}
</error_traceback>

<stdout>
{failure['stdout'][:500]}
</stdout>

{source_block}

Respond ONLY with a JSON object (no markdown, no explanation outside JSON):
{{
  "root_cause": "one-line summary",
  "affected_file": "relative/path/to/file.py or null",
  "fix_description": "what the fix does",
  "patch": {{
    "search":  "exact string to find in the file (must be unique)",
    "replace": "replacement string"
  }}
}}

Rules:
- "search" must be an exact substring of the file (not a regex, not truncated)
- If you cannot determine a safe patch, set "patch" to null
- The fix must be minimal — do not refactor unrelated code
"""


def apply_patch(patch: dict, repo_path: str, affected_file: str) -> bool:
    """Apply search→replace patch to the affected file."""
    if not affected_file:
        print("[ai_fix_agent] No affected file specified — skipping patch.")
        return False

    fpath = Path(repo_path) / affected_file
    if not fpath.exists():
        print(f"[ai_fix_agent] File not found: {fpath}")
        return False

    content = fpath.read_text(encoding="utf-8")
    search  = patch.get("search", "")
    replace = patch.get("replace", "")

    if not search:
        print("[ai_fix_agent] Empty search string — skipping patch.")
        return False

    if search not in content:
        print(f"[ai_fix_agent] Search string not found in {affected_file}")
        return False

    new_content = content.replace(search, replace, 1)
    fpath.write_text(new_content, encoding="utf-8")
    print(f"[ai_fix_agent] ✅ Patched {affected_file}")
    return True


def fix_failure(client: Groq, failure: dict, repo_path: str) -> dict:
    """Ask Groq to fix one failing test and apply the patch."""
    source_path = _extract_source_path_from_nodeid(failure["nodeid"])
    source_code = _read_source_file(source_path, repo_path)

    prompt = build_groq_prompt(failure, source_code)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1024,
            )
            raw = response.choices[0].message.content.strip()
            # Strip markdown fences if hallucinated
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)
            print(f"[ai_fix_agent] Root cause: {result.get('root_cause', '?')}")
            print(f"[ai_fix_agent] Fix: {result.get('fix_description', '?')}")

            patch = result.get("patch")
            affected_file = result.get("affected_file")
            if patch and affected_file:
                apply_patch(patch, repo_path, affected_file)

            return result

        except (json.JSONDecodeError, KeyError) as e:
            print(f"[ai_fix_agent] Attempt {attempt} parse error: {e}")
            if attempt == MAX_RETRIES:
                print("[ai_fix_agent] Max retries reached — skipping this failure.")
                return {"root_cause": "parse error", "patch": None}

    return {}


def main():
    parser = argparse.ArgumentParser(description="AI Fix Agent")
    parser.add_argument("--test-results", required=True, help="Path to test_results.json")
    parser.add_argument("--repo-path",    required=True, help="Root of the repository")
    args = parser.parse_args()

    if not GROQ_API_KEY:
        print("[ai_fix_agent] GROQ_API_KEY not set — exiting.")
        sys.exit(1)

    failures = load_failures(args.test_results)
    if not failures:
        print("[ai_fix_agent] No failures found — nothing to fix.")
        sys.exit(0)

    print(f"[ai_fix_agent] Found {len(failures)} failing test(s).")
    client = Groq(api_key=GROQ_API_KEY)

    results = []
    for f in failures:
        print(f"\n─── Fixing: {f['nodeid']} ───")
        r = fix_failure(client, f, args.repo_path)
        results.append(r)

    # Write fix summary
    summary_path = Path(args.repo_path) / "ai_fix_summary.json"
    summary_path.write_text(json.dumps(results, indent=2))
    print(f"\n[ai_fix_agent] Summary written → {summary_path}")
    print("[ai_fix_agent] Done. Git commit step will pick up any changed files.")


if __name__ == "__main__":
    main()