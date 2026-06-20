"""
agent/ai_fix_agent.py
---------------------
AI agent that analyzes test failures and proposes code fixes.
"""

import os
import sys
import json
import re
import difflib
from pathlib import Path
from typing import Dict, List, Optional

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
        self.confidence_threshold = 0.7

    def analyze_and_fix(self, test_output_path: str, mode: str = "propose-only") -> Dict:
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
            "app/templates/login.html",
            "app/templates/dashboard.html",
            "app/templates/base.html",
            "app/static/js/app.js",
            "app/static/css/style.css",
        ]
        
        for path in source_paths:
            full_path = Path(os.path.dirname(__file__)).parent / path
            if not full_path.exists():
                continue
                
            with open(full_path) as f:
                content = f.read()
            
            score = 0.0
            reasons = []
            
            for failure in failures:
                error_text = f"{failure['test_name']} {failure['error_summary']}".lower()
                file_name = path.split("/")[-1].lower()
                
                if file_name in error_text:
                    score += 0.4
                    reasons.append(f"mentioned: {file_name}")
                
                if failure.get("test_file"):
                    test_name = Path(failure["test_file"]).stem
                    if "login" in test_name and "login" in path:
                        score += 0.3
                    elif "dashboard" in test_name and "dashboard" in path:
                        score += 0.3
            
            if "KeyError" in str(failures) and "main.py" in path:
                score += 0.3
                reasons.append("KeyError → backend")
            
            files[path] = {
                "score": min(score, 1.0),
                "content": content,
                "reasons": reasons,
            }
        
        return dict(sorted(files.items(), key=lambda x: x[1]["score"], reverse=True))

    def _generate_fixes(self, failures: List[Dict], source_files: Dict[str, str], jira_ticket: Optional[str]) -> Dict[str, str]:
        failure_context = json.dumps([{
            "test": f["test_name"],
            "error": f["error_summary"][:300],
        } for f in failures], indent=2)

        ticket_ref = f"\nRelated Jira Ticket: {jira_ticket}" if jira_ticket else ""

        prompt = f"""You are an expert code reviewer. Fix these test failures.{ticket_ref}

FAILURES:
{failure_context}

SOURCE FILES:
"""
        for path, content in source_files.items():
            prompt += f"\n--- {path} ---\n{content[:2000]}\n"

        prompt += """
RULES:
1. MINIMAL changes only
2. Preserve all functionality
3. Do NOT change file paths
4. Return ONLY JSON: {"file_path": "full new content"}"""

        try:
            response = self.client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "Precise code fixer. Output only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.05,
                max_tokens=4000,
            )
            
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
            
            fixes = json.loads(raw)
            validated = {}
            for path, content in fixes.items():
                if path in source_files and len(content) > 100:
                    validated[path] = content
            
            return validated
            
        except Exception as e:
            print(f"⚠️ Fix generation failed: {e}")
            return {}

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
        
        for path, content in fixes.items():
            safe_name = path.replace("/", "_")
            (AI_FIX_DIR / f"fix_{safe_name}").write_text(content)
        
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


from datetime import datetime

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