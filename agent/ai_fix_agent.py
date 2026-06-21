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

# Model used for fix generation. Separate from the analysis model so we can
# pick a smaller/faster model with a higher TPD limit to avoid rate limits.
# llama-3.1-8b-instant has 500k TPD vs llama-3.3-70b-versatile's 100k TPD.
FIX_MODEL = os.getenv("GROQ_FIX_MODEL", "llama-3.1-8b-instant")

# Files that are never worth sending to the LLM — they don't contain logic
# that could cause the test failures we care about, and including them wastes
# tokens pushing us into rate-limit territory.
EXCLUDED_FROM_FIX = {
    "app/static/css/style.css",
    "app/templates/base.html",
    "app/templates/items.html",
    "app/templates/item_detail.html",
}

# Maximum number of files to include in a single LLM call.
# The top-N by score are used; the rest are excluded to save tokens.
MAX_FILES_PER_CALL = 3

# Maximum characters of a single file's content sent to the LLM.
# Files longer than this are truncated with a note so the model still
# understands the structure without consuming excessive tokens.
MAX_FILE_CHARS = 3000


class AIFixAgent:
    def __init__(self):
        self.client = Groq(
            api_key=settings.GROQ_API_KEY,
            http_client=httpx.Client(verify=False),
        )
        self.confidence_threshold = 0.5

    # These strings MUST be present in any proposed app/main.py.
    MAIN_PY_REQUIRED = [
        "@app.route('/health')",
        "app.run(host=\"0.0.0.0\", port=5000",
        "debug=False",
    ]

    def propose_fix_batch(self, bug_report_paths: List[str], mode: str = "propose-only") -> Dict:
        """
        Process ALL bug reports in a single LLM call so every failing test
        is addressed together and no fix overwrites another.
        """
        print(f"\n{'='*60}")
        print(f"🤖 AI FIX AGENT (BATCH)")
        print(f" Reports: {len(bug_report_paths)}")
        print(f" Mode: {mode}")
        print(f" Fix model: {FIX_MODEL}")
        print(f"{'='*60}")

        all_failures = []
        jira_ticket = None

        for br_path in bug_report_paths:
            try:
                with open(br_path, 'r') as f:
                    bug_report = json.load(f)
            except Exception as e:
                print(f"❌ Failed to read {br_path}: {e}")
                continue

            metadata = bug_report.get("metadata", {})
            test_name = metadata.get("test_name", "unknown")
            error_type = metadata.get("error_type", "Unknown")
            affected_component = metadata.get("affected_component", "unknown")

            if not jira_ticket:
                jira_ticket = bug_report.get("ticket_key") or bug_report.get("jira_ticket")
            if not jira_ticket:
                jira_ticket = self._get_jira_ticket_from_cache_or_files()

            print(f"\n📋 Bug: {test_name}")
            print(f"   🔴 {error_type} | 🏗️ {affected_component}")

            raw_test_file = (metadata.get("test_file") or "").replace("\\", "/")
            if "tests/" in raw_test_file:
                rel_test_file = "tests/" + raw_test_file.split("tests/")[-1]
            else:
                rel_test_file = raw_test_file

            all_failures.append({
                "test_name": test_name,
                "test_file": rel_test_file,
                "error_summary": bug_report.get("title", "") + " " + bug_report.get("description", ""),
                "error_type": error_type.lower(),
                "component": affected_component,
                "description": bug_report.get("description", ""),
            })

        if not all_failures:
            return {"fixed": False, "reason": "no_valid_bug_reports"}

        if jira_ticket:
            print(f"\n🎫 Jira Ticket: {jira_ticket}")

        file_scores = self._score_files_for_fixing(all_failures)
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

        # Cap to top MAX_FILES_PER_CALL files to control token usage
        high_confidence = dict(list(high_confidence.items())[:MAX_FILES_PER_CALL])

        print(f"\n📎 Files selected for fixing ({len(high_confidence)} of {len(file_scores)} scored, capped at {MAX_FILES_PER_CALL}):")
        for path, info in high_confidence.items():
            content_len = len(info.get('content', ''))
            sent_len = min(content_len, MAX_FILE_CHARS)
            print(f"   {path}: score={info['score']:.2f}, chars={content_len} (sending {sent_len}) ({', '.join(info.get('reasons', []))})")

        source_files = {k: v['content'] for k, v in high_confidence.items()}
        fixes = self._generate_fixes(all_failures, source_files, jira_ticket)

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
            "bugs_addressed": len(all_failures),
            "approval_required": mode == "propose-only",
        }

    def propose_fix(self, bug_report_path: str, mode: str = "propose-only") -> Dict:
        """Single bug report — delegates to propose_fix_batch. Kept for compatibility."""
        return self.propose_fix_batch([bug_report_path], mode=mode)

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

        high_confidence = dict(list(high_confidence.items())[:MAX_FILES_PER_CALL])
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
            "app/static/js/app.js",
            "app/static/js/login.js",
        ]

        # CSS and structural templates never need changing for logic bugs — skip
        # them entirely so they never consume tokens in the LLM call.
        source_paths = [p for p in source_paths if p not in EXCLUDED_FROM_FIX]

        # Discover test files BEFORE the outer loop.
        for failure in failures:
            raw_test_file = (failure.get("test_file") or "").replace("\\", "/")
            if "tests/" in raw_test_file:
                rel_test_path = "tests/" + raw_test_file.split("tests/")[-1]
                if rel_test_path not in source_paths and rel_test_path not in EXCLUDED_FROM_FIX:
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
                component = failure.get("component", "")
                test_file = failure.get("test_file", "")
                error_type = (failure.get("error_type") or "").lower()
                error_text = (failure.get("error_summary") or "").lower()
                file_name = Path(path).name

                if component and component.lower() in path.lower():
                    score += 0.4
                    reasons.append(f"component: {component}")
                    component_match = True

                if test_file:
                    test_stem = Path(test_file).stem
                    app_stem  = test_stem.replace("test_", "")
                    if test_stem in path.lower() or app_stem in path.lower():
                        score += 0.3
                        reasons.append(f"test_file: {test_stem}")

                if error_type == "assertionerror" and any(x in path.lower() for x in ["main.py", "routes.py", "views.py", "auth.py"]):
                    score += 0.2
                    reasons.append("assertionerror → backend")

                if error_type in ("timeouterror", "timeoutexception"):
                    if any(x in path.lower() for x in ["main.py", "routes.py", "auth.py"]):
                        score += 0.25
                        reasons.append("timeout → backend")

                if file_name and file_name in error_text:
                    score += 0.3
                    reasons.append(f"mentioned: {file_name}")

                if "login" in error_text and "login" in path.lower():
                    score += 0.25
                    reasons.append("login context")

                if "dashboard" in error_text and "dashboard" in path.lower():
                    score += 0.25
                    reasons.append("dashboard context")

                if any(x in error_text for x in ["unauthorized", "403", "auth", "login required"]):
                    if any(x in path.lower() for x in ["auth", "login", "main.py", "routes"]):
                        score += 0.3
                        reasons.append("auth context")

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

    def _truncate_content(self, path: str, content: str) -> str:
        """
        Truncate file content to MAX_FILE_CHARS to control token usage.
        Adds a note at the end so the LLM knows the file continues.
        """
        if len(content) <= MAX_FILE_CHARS:
            return content
        truncated = content[:MAX_FILE_CHARS]
        # Try to truncate at a newline boundary so we don't cut mid-line
        last_newline = truncated.rfind('\n')
        if last_newline > MAX_FILE_CHARS * 0.8:
            truncated = truncated[:last_newline]
        remaining = len(content) - len(truncated)
        return truncated + f"\n# ... [{remaining} more chars truncated to save tokens] ..."

    def _generate_fixes(self, failures: List[Dict], source_files: Dict[str, str], jira_ticket: Optional[str]) -> Dict[str, str]:
        # Build compact failure summary — skip the full description to save tokens
        failure_context = json.dumps([{
            "test": f["test_name"],
            "error": (f.get("error_summary") or "")[:150],   # trimmed from 300
            "type": f.get("error_type", "Unknown"),
            "component": f.get("component", "unknown"),
        } for f in failures], indent=2)

        ticket_ref = f"\nJira: {jira_ticket}" if jira_ticket else ""

        prompt = f"""Fix ALL these test failures.{ticket_ref}

FAILURES ({len(failures)} total):
{failure_context}

FILES:
"""
        for path, content in source_files.items():
            truncated = self._truncate_content(path, content)
            prompt += f"\n--- {path} ---\n{truncated}\n"

        prompt += """
RULES:
1. Fix ALL failures. Minimal changes only.
2. app/main.py MUST keep: @app.route('/health'), app.run(host="0.0.0.0", port=5000, debug=False), all existing routes.
3. Return ONLY valid JSON: {{"file/path.py": "full file content", ...}}
4. No markdown, no explanations.
5. If admin login fails because test sends "password123" but USERS has "wrongpassword", fix the USERS dict.
6. If /dashboard returns redirect instead of 403, use abort(403) + @app.errorhandler(403).
7. If JS alerts exist but are not visible, add el.style.display='block' alongside classList.add('show')."""

        # Estimate tokens: ~4 chars per token
        estimated_tokens = len(prompt) // 4
        print(f"📊 Estimated prompt tokens: ~{estimated_tokens} (prompt chars: {len(prompt)})")

        try:
            response = self.client.chat.completions.create(
                model=FIX_MODEL,
                messages=[
                    {"role": "system", "content": "Output ONLY valid JSON mapping file paths to complete fixed file content. No markdown. No explanations."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.05,
                max_tokens=2500,   # reduced from 4000 — fixes are small diffs
            )

            raw = response.choices[0].message.content.strip()
            print(f"📊 Response tokens used: input={response.usage.prompt_tokens}, output={response.usage.completion_tokens}, total={response.usage.total_tokens}")
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
        """Robust JSON extraction from LLM response."""
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()

        best_fixes = {}

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return self._validate_fixes(parsed, source_files)
        except json.JSONDecodeError:
            pass

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

        fixed_raw = re.sub(r',\s*}', '}', raw)
        fixed_raw = re.sub(r',\s*]', ']', fixed_raw)
        fixed_raw = fixed_raw.replace("'", '"')

        try:
            parsed = json.loads(fixed_raw)
            if isinstance(parsed, dict):
                return self._validate_fixes(parsed, source_files)
        except json.JSONDecodeError:
            pass

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
        """Validate fixes. Reject app/main.py if it drops critical routes."""
        validated = {}
        for path, content in parsed.items():
            if not isinstance(path, str) or not isinstance(content, str):
                continue
            if not (path in source_files or path.startswith("app/") or path.startswith("tests/")):
                continue
            if len(content) < 50:
                print(f"⚠️ Skipping {path} — too short ({len(content)} chars), likely a stub")
                continue

            if path == "app/main.py":
                missing = [req for req in self.MAIN_PY_REQUIRED if req not in content]
                if missing:
                    print(f"❌ Rejecting AI fix for app/main.py — missing required strings:")
                    for m in missing:
                        print(f"   • {m!r}")
                    print(f"   Keeping original app/main.py.")
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