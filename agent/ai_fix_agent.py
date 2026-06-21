"""
agent/ai_fix_agent.py
-------------------
AI agent that analyzes test failures and proposes code fixes.
"""

import os
import sys
import json
import re
import difflib
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from groq import Groq
import httpx
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config.settings import settings

ARTIFACT_DIR = Path(os.path.dirname(__file__)).parent / "artifacts"
AI_FIX_DIR = ARTIFACT_DIR / "ai_fixes"


class AIFixAgent:
    def __init__(self):
        self.client = Groq(
            api_key=settings.GROQ_API_KEY,
            http_client=httpx.Client(verify=False),
        )
        self.confidence_threshold = 0.5

    # These strings MUST be present in any proposed app/main.py.
    # If the LLM drops any of them, the fix is rejected and the original is kept.
    MAIN_PY_REQUIRED = [
        "@app.route('/health')",
        "app.run(host=\"0.0.0.0\", port=5000",
        "debug=False",
    ]

    def propose_fix(self, bug_report_path: str, mode: str = "propose-only") -> Dict:
        """
        Reads a bug report JSON and proposes a fix.
        """
        print(f"\n{'='*60}")
        print(f"🤖 AI FIX AGENT")
        print(f" Input: {bug_report_path}")
        print(f" Mode: {mode}")
        print(f"{'='*60}")

        try:
            with open(bug_report_path, 'r') as f:
                bug_report = json.load(f)
        except Exception as e:
            print(f"❌ Failed to read bug report: {e}")
            return {"fixed": False, "reason": f"read_error: {e}"}

        metadata = bug_report.get("metadata", {})
        test_name = metadata.get("test_name", "unknown")
        error_type = metadata.get("error_type", "Unknown")
        affected_component = metadata.get("affected_component", "unknown")

        jira_ticket = bug_report.get("ticket_key") or bug_report.get("jira_ticket")
        if not jira_ticket:
            jira_ticket = self._get_jira_ticket_from_cache_or_files()

        print(f"📋 Bug Report: {test_name}")
        print(f"🔴 Error Type: {error_type}")
        print(f"🏗️ Component: {affected_component}")
        if jira_ticket:
            print(f"🎫 Jira Ticket: {jira_ticket}")

        # Normalize test_file from absolute CI path to repo-relative path.
        # e.g. /home/runner/work/.../tests/test_login.py → tests/test_login.py
        raw_test_file = (metadata.get("test_file") or "").replace("\\", "/")
        if "tests/" in raw_test_file:
            rel_test_file = "tests/" + raw_test_file.split("tests/")[-1]
        else:
            rel_test_file = raw_test_file

        failures = [{
            "test_name": test_name,
            "test_file": rel_test_file,
            "error_summary": bug_report.get("title", "") + " " + bug_report.get("description", ""),
            "error_type": error_type.lower(),
            "component": affected_component,
            "description": bug_report.get("description", ""),
        }]

        file_scores = self._score_files_for_fixing(failures)
        high_confidence = {k: v for k, v in file_scores.items() if v['score'] >= self.confidence_threshold}

        if not high_confidence:
            print("⚠️ No high-confidence files found. Trying component-based fallback...")
            component_files = {k: v for k, v in file_scores.items()
                             if v.get('component_match') or v['score'] > 0.1}
            if component_files:
                print(f"✅ Found {len(component_files)} component-matched files")
                high_confidence = component_files
            else:
                print("⚠️ Low confidence — deferring to human")
                return {
                    "fixed": False,
                    "reason": "low_confidence",
                    "jira_ticket": jira_ticket,
                }

        print(f"📎 Scoring files for fixing...")
        for path, info in high_confidence.items():
            print(f"   {path}: score={info['score']:.2f} ({', '.join(info.get('reasons', []))})")

        source_files = {k: v['content'] for k, v in high_confidence.items()}
        fixes = self._generate_fixes(failures, source_files, jira_ticket)

        if not fixes:
            return {
                "fixed": False,
                "reason": "generation_failed",
                "jira_ticket": jira_ticket,
            }

        diffs = self._generate_diffs(fixes, source_files)
        self._save_fixes(fixes, diffs, mode, file_scores, jira_ticket)

        return {
            "fixed": True,
            "mode": mode,
            "fixes": fixes,
            "diffs": diffs,
            "jira_ticket": jira_ticket,
            "approval_required": mode == "propose-only",
        }

    def _get_jira_ticket_from_cache_or_files(self) -> Optional[str]:
        """Try to find jira ticket from various sources."""
        bug_reports = list(ARTIFACT_DIR.glob("bug_reports/bug_report_*.json"))
        for br in bug_reports:
            try:
                with open(br) as f:
                    data = json.load(f)
                if data.get("ticket_key"):
                    return data.get("ticket_key")
            except:
                pass

        cache_file = ARTIFACT_DIR / "dedup_cache.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    cache = json.load(f)
                for entry in cache.values():
                    if entry.get("ticket_key"):
                        return entry.get("ticket_key")
            except:
                pass
        return None

    def analyze_and_fix(self, test_output_path: str, mode: str = "propose-only") -> Dict:
        """Original method for backward compatibility."""
        print(f"\n{'='*60}")
        print(f"🤖 AI FIX AGENT")
        print(f" Mode: {mode}")
        print(f"{'='*60}")

        failures = self._read_test_failures(test_output_path)
        if not failures:
            return {"fixed": False, "reason": "no_failures"}

        print(f"❌ {len(failures)} failure(s)")

        jira_ticket = self._get_jira_ticket_reference()

        file_scores = self._score_files_for_fixing(failures)
        high_confidence = {k: v for k, v in file_scores.items() if v['score'] >= self.confidence_threshold}

        if not high_confidence:
            print("⚠️ Low confidence — deferring to human")
            return {
                "fixed": False,
                "reason": "low_confidence",
                "jira_ticket": jira_ticket,
            }

        source_files = {k: v['content'] for k, v in high_confidence.items()}
        fixes = self._generate_fixes(failures, source_files, jira_ticket)

        if not fixes:
            return {
                "fixed": False,
                "reason": "generation_failed",
                "jira_ticket": jira_ticket,
            }

        diffs = self._generate_diffs(fixes, source_files)
        self._save_fixes(fixes, diffs, mode, file_scores, jira_ticket)

        return {
            "fixed": True,
            "mode": mode,
            "fixes": fixes,
            "diffs": diffs,
            "jira_ticket": jira_ticket,
            "approval_required": mode == "propose-only",
        }

    def _get_jira_ticket_reference(self) -> Optional[str]:
        bug_reports = list(ARTIFACT_DIR.glob("bug_reports/bug_report_*.json"))
        if bug_reports:
            try:
                with open(bug_reports[0]) as f:
                    data = json.load(f)
                return data.get("ticket_key")
            except:
                pass
        return None

    def _read_test_failures(self, test_output_path: str) -> List[Dict]:
        failures = []
        metadata_files = sorted(ARTIFACT_DIR.glob("failure_*.json"))

        for mf in metadata_files:
            with open(mf) as f:
                meta = json.load(f)
            failures.append({
                "test_name": meta["test_name"],
                "test_file": meta.get("file"),
                "error_summary": meta.get("failure_summary", ""),
            })
        return failures

    def _score_files_for_fixing(self, failures: List[Dict]) -> Dict[str, Dict]:
        files = {}

        source_paths = [
            "app/main.py",
            "app/routes.py",
            "app/auth.py",
            "app/login.py",
            "app/views.py",
            "app/templates/login.html",
            "app/templates/dashboard.html",
            "app/templates/base.html",
            "app/templates/index.html",
            "app/static/js/app.js",
            "app/static/js/login.js",
            "app/static/css/style.css",
        ]

        # Discover test files BEFORE the outer loop so they are read and scored.
        # Previously this was inside the inner loop — paths got appended after
        # the outer loop had already passed them, so the inner loop never ran
        # for those paths, leaving component/test_file/etc. undefined → NameError.
        for failure in failures:
            raw_test_file = (failure.get("test_file") or "").replace("\\", "/")
            if "tests/" in raw_test_file:
                rel_test_path = "tests/" + raw_test_file.split("tests/")[-1]
                if rel_test_path not in source_paths:
                    source_paths.append(rel_test_path)

        for path in source_paths:
            full_path = Path(os.path.dirname(__file__)).parent / path
            if not full_path.exists():
                continue

            with open(full_path) as f:
                content = f.read()

            score = 0.0
            reasons = []
            component_match = False

            for failure in failures:
                # All variables initialized inside loop — never undefined.
                component = failure.get("component", "")
                test_file = failure.get("test_file", "")
                error_type = (failure.get("error_type") or "").lower()
                error_text = (failure.get("error_summary") or "").lower()
                file_name = Path(path).name

                # Component-based matching (strong signal)
                if component and component.lower() in path.lower():
                    score += 0.4
                    reasons.append(f"component: {component}")
                    component_match = True

                # Test file name matching
                if test_file:
                    test_stem = Path(test_file).stem           # e.g. "test_login"
                    app_stem  = test_stem.replace("test_", "") # e.g. "login"
                    if test_stem in path.lower() or app_stem in path.lower():
                        score += 0.3
                        reasons.append(f"test_file: {test_stem}")

                # Error type matching
                if error_type == "assertionerror" and any(x in path.lower() for x in ["main.py", "routes.py", "views.py", "auth.py"]):
                    score += 0.2
                    reasons.append("assertionerror → backend")

                if error_type in ("timeouterror", "timeoutexception"):
                    if any(x in path.lower() for x in ["main.py", "routes.py", "auth.py"]):
                        score += 0.25
                        reasons.append("timeout → backend")

                # File name mentioned in error text
                if file_name and file_name in error_text:
                    score += 0.3
                    reasons.append(f"mentioned: {file_name}")

                # Login-related errors → login files
                if "login" in error_text and "login" in path.lower():
                    score += 0.25
                    reasons.append("login context")

                # Dashboard-related errors → dashboard files
                if "dashboard" in error_text and "dashboard" in path.lower():
                    score += 0.25
                    reasons.append("dashboard context")

                # Unauthorized/403 errors → auth files
                if any(x in error_text for x in ["unauthorized", "403", "auth", "login required"]):
                    if any(x in path.lower() for x in ["auth", "login", "main.py", "routes"]):
                        score += 0.3
                        reasons.append("auth context")

                # KeyError handling
                if "keyerror" in error_text and "main.py" in path:
                    score += 0.3
                    reasons.append("KeyError → backend")

            files[path] = {
                "score": min(score, 1.0),
                "content": content,
                "reasons": list(set(reasons)),
                "component_match": component_match,
            }

        return dict(sorted(files.items(), key=lambda x: x[1]["score"], reverse=True))

    def _generate_fixes(self, failures: List[Dict], source_files: Dict[str, str], jira_ticket: Optional[str]) -> Dict[str, str]:
        failure_context = json.dumps([{
            "test": f["test_name"],
            "error": f["error_summary"][:300],
            "type": f.get("error_type", "Unknown"),
            "component": f.get("component", "unknown"),
        } for f in failures], indent=2)

        ticket_ref = f"\nRelated Jira Ticket: {jira_ticket}" if jira_ticket else ""

        prompt = f"""You are an expert code reviewer. Fix these test failures.{ticket_ref}

FAILURES:
{failure_context}

SOURCE FILES:
"""
        for path, content in source_files.items():
            prompt += f"\n--- {path} ---\n{content}\n"

        prompt += """
RULES:
1. MINIMAL changes only — change as few lines as possible.
2. app/main.py HARD CONSTRAINTS — you MUST preserve ALL of the following exactly, no exceptions:
   - The @app.route('/health') route that returns jsonify({'status': 'ok'}), 200
   - app.run(host="0.0.0.0", port=5000, debug=False) — do NOT change host, port, or debug value
   - Every existing @app.route definition — do NOT remove or rename any route
   - The USERS and ITEMS dicts (only change a value if the fix specifically requires it)
3. Do NOT change file paths.
4. Do NOT remove any existing @app.route definitions from any file.
5. Return ONLY a single JSON object with file paths as keys and complete file content as values.
6. Do NOT include markdown formatting, explanations, or multiple JSON objects.
7. Example format: {"app/main.py": "from flask import Flask..."}
8. Decide WHERE the actual bug is before fixing. If a test file is included above:
   - If the test's expected input/output doesn't match constants already defined
     in the application code (e.g. a hardcoded username/password dict), and the
     test's own docstring/class name implies it should represent a VALID/working
     case, the bug is almost certainly wrong test data — fix the test file's
     literal values to match the application's existing constants. Do NOT change
     the application's constants to match the test.
   - If the application returns the wrong status code, wrong response shape, or
     incorrect business logic relative to what the test (correctly) expects,
     fix the application file instead.
9. NEVER weaken, remove, or loosen a test assertion to make it pass artificially.
   You may only correct factual test input (e.g. a typo'd credential, a wrong
   expected value that contradicts the app's own documented behavior). Do not
   change what a test checks for — only correct what's clearly wrong input/data.
10. If unsure whether to fix the app or the test, prefer fixing the application
    file — only fix a test file when the evidence that the test itself is wrong
    is unambiguous."""

        try:
            response = self.client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You are a precise code fixer. Output ONLY valid JSON. No markdown. No explanations."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.05,
                max_tokens=4000,
            )

            raw = response.choices[0].message.content.strip()
            fixes = self._extract_json_from_response(raw, source_files)

            if fixes:
                return fixes
            else:
                print(f"⚠️ Could not extract valid JSON from response")
                print(f"Raw response preview: {raw[:200]}...")
                return {}

        except Exception as e:
            print(f"⚠️ Fix generation failed: {e}")
            return {}

    def _extract_json_from_response(self, raw: str, source_files: Dict[str, str]) -> Dict[str, str]:
        """
        Robust JSON extraction from LLM response.
        Handles markdown, multiple JSON objects, extra text, etc.
        """
        # Remove markdown code blocks
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()

        best_fixes = {}

        # Strategy 1: Try parsing the whole thing first
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return self._validate_fixes(parsed, source_files)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Find all JSON-like objects using brace matching
        depth = 0
        start = -1
        for i, char in enumerate(raw):
            if char == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and start != -1:
                    candidate = raw[start:i+1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            validated = self._validate_fixes(parsed, source_files)
                            if validated:
                                for k, v in validated.items():
                                    if k not in best_fixes or len(v) > len(best_fixes[k]):
                                        best_fixes[k] = v
                    except json.JSONDecodeError:
                        pass
                    start = -1

        if best_fixes:
            return best_fixes

        # Strategy 3: Try to fix common JSON issues and retry
        fixed_raw = raw
        fixed_raw = re.sub(r',\s*}', '}', fixed_raw)
        fixed_raw = re.sub(r',\s*]', ']', fixed_raw)
        fixed_raw = fixed_raw.replace("'", '"')

        try:
            parsed = json.loads(fixed_raw)
            if isinstance(parsed, dict):
                return self._validate_fixes(parsed, source_files)
        except json.JSONDecodeError:
            pass

        # Strategy 4: Try first { to last }
        first_brace = raw.find('{')
        last_brace = raw.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            try:
                parsed = json.loads(raw[first_brace:last_brace+1])
                if isinstance(parsed, dict):
                    return self._validate_fixes(parsed, source_files)
            except json.JSONDecodeError:
                pass

        return {}

    def _validate_fixes(self, parsed: Dict, source_files: Dict[str, str]) -> Dict[str, str]:
        """
        Validate that parsed fixes are for known source files and have reasonable content.
        For app/main.py specifically, enforce that critical strings are present —
        the LLM tends to replace it with a minimal stub that drops /health and
        changes app.run() config, breaking the CI health check.
        """
        validated = {}
        for path, content in parsed.items():
            if not isinstance(path, str) or not isinstance(content, str):
                continue
            if not (path in source_files or path.startswith("app/") or path.startswith("tests/")):
                continue
            if len(content) < 50:
                print(f"⚠️ Skipping {path} — content too short ({len(content)} chars), likely a stub")
                continue

            # Hard guard for app/main.py: reject any version that drops
            # the /health route or changes the app.run() binding.
            if path == "app/main.py":
                missing = [req for req in self.MAIN_PY_REQUIRED if req not in content]
                if missing:
                    print(f"❌ Rejecting AI fix for app/main.py — missing required strings:")
                    for m in missing:
                        print(f"   • {m!r}")
                    print(f"   Falling back to original app/main.py to avoid breaking Flask startup.")
                    # Keep the original so it still appears in the PR diff correctly
                    if path in source_files:
                        validated[path] = source_files[path]
                    continue

            validated[path] = content
            print(f"✅ Valid fix for {path} ({len(content)} chars)")
        return validated

    def _generate_diffs(self, fixes: Dict[str, str], originals: Dict[str, str]) -> Dict[str, str]:
        diffs = {}
        for path, new_content in fixes.items():
            if path in originals:
                old_lines = originals[path].splitlines(keepends=True)
                new_lines = new_content.splitlines(keepends=True)
                diff = difflib.unified_diff(
                    old_lines, new_lines,
                    fromfile=f"a/{path}", tofile=f"b/{path}",
                    lineterm=""
                )
                diffs[path] = "".join(diff)
        return diffs

    def _save_fixes(self, fixes: Dict, diffs: Dict, mode: str, scores: Dict, jira_ticket: Optional[str]) -> None:
        AI_FIX_DIR.mkdir(parents=True, exist_ok=True)

        manifest = {}
        for path, content in fixes.items():
            safe_name = path.replace("/", "_")
            fix_filename = f"fix_{safe_name}"
            (AI_FIX_DIR / fix_filename).write_text(content)
            manifest[path] = fix_filename

        # manifest maps original repo path -> generated fix filename, so
        # the workflow doesn't have to reverse-engineer the path from the
        # filename. That reversal breaks for paths like tests/test_login.py,
        # since a blind "_" -> "/" swap would wrongly split "test_login.py"
        # into "test/login.py".
        manifest_path = AI_FIX_DIR / "fix_manifest.json"
        existing_manifest = {}
        if manifest_path.exists():
            try:
                existing_manifest = json.loads(manifest_path.read_text())
            except Exception:
                existing_manifest = {}
        existing_manifest.update(manifest)
        manifest_path.write_text(json.dumps(existing_manifest, indent=2))

        for path, diff in diffs.items():
            safe_name = path.replace("/", "_")
            (AI_FIX_DIR / f"diff_{safe_name}.patch").write_text(diff)

        meta = {
            "mode": mode,
            "timestamp": str(datetime.now().isoformat()),
            "files_changed": list(fixes.keys()),
            "confidence_scores": {k: v["score"] for k, v in scores.items()},
            "approval_status": "pending",
            "jira_ticket": jira_ticket,
        }
        (AI_FIX_DIR / "fix_metadata.json").write_text(json.dumps(meta, indent=2))

        status_text = f"Fix proposed\nJira: {jira_ticket or 'N/A'}\nFiles: {', '.join(fixes.keys())}\nStatus: PENDING_APPROVAL\n"
        (AI_FIX_DIR / "fix_applied.txt").write_text(status_text)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("test_output")
    parser.add_argument("--mode", default="propose-only")
    args = parser.parse_args()

    agent = AIFixAgent()
    result = agent.analyze_and_fix(args.test_output, args.mode)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()