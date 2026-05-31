"""
agent/failure_agent.py
-----------------------
Core AI agent — reads failure metadata, calls Groq, produces bug_report.json.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import re
import json
import urllib3
import httpx
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from datetime import datetime
from groq import Groq

from config.settings import settings
from agent.prompts.analysis_prompt import (
    ANALYSIS_SYSTEM_PROMPT,
    build_analysis_prompt,
)
from mcp_server.jira_tools import _make_test_label

ARTIFACT_DIR      = os.path.join(os.path.dirname(__file__), "..", "artifacts")
TEAM_MAPPING_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "team_mapping.json")


class FailureAnalysisAgent:

    def __init__(self):
        self.client = Groq(
            api_key     = settings.GROQ_API_KEY,
            http_client = httpx.Client(verify=False),
        )
        self.team_mapping = self._load_team_mapping()

    def analyze_failure(self, metadata_path: str) -> dict:
        print(f"\n{'='*60}")
        print(f"🤖 AI AGENT STARTING")
        print(f"   Input: {metadata_path}")
        print(f"{'='*60}")

        metadata  = self._load_metadata(metadata_path)
        artifacts = self._load_artifacts(metadata)

        print(f"✅ Artifacts loaded")
        print(f"   Test : {metadata['test_name']}")
        print(f"   Stack: {len(artifacts['stacktrace'])} chars")
        print(f"   Logs : {len(artifacts.get('console_logs', ''))} chars")

        print(f"\n🧠 Sending to Groq ({settings.GROQ_MODEL})...")
        analysis = self._ai_analyze(artifacts)
        print(f"✅ Groq analysis complete")
        print(f"   Error type       : {analysis.get('error_type')}")
        print(f"   Affected component: {analysis.get('affected_component')}")
        print(f"   Root cause       : {analysis.get('root_cause', '')[:80]}...")

        severity, priority = self._classify(analysis, artifacts["stacktrace"])
        print(f"\n📊 Classification: severity={severity}  priority={priority}")

        assignee_info = self._get_assignee(analysis.get("affected_component", "default"))
        print(f"👤 Assignee: {assignee_info['name']} ({assignee_info['assignee']})")

        bug_report  = self._build_report(metadata, analysis, severity, priority, assignee_info)
        output_path = self._save_report(bug_report, metadata["test_name"])
        print(f"\n💾 Bug report saved → {output_path}")

        return {"bug_report": bug_report, "output_path": output_path, "metadata": metadata}

    # ── Data loading ──────────────────────────────────────────────────

    def _load_metadata(self, path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _load_artifacts(self, metadata: dict) -> dict:
        artifacts = {
            "test_name": metadata["test_name"],
            "file":      metadata["file"],
            "duration":  metadata.get("duration", 0),
            "timestamp": metadata["timestamp"],
        }

        trace_path = metadata.get("stacktrace_file")
        if trace_path and os.path.exists(trace_path):
            with open(trace_path, encoding="utf-8") as f:
                artifacts["stacktrace"] = f.read()[:settings.MAX_STACKTRACE_LENGTH]
        else:
            artifacts["stacktrace"] = metadata.get("failure_summary", "No stack trace available.")

        log_path = metadata.get("console_log_file")
        if log_path and os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as f:
                artifacts["console_logs"] = f.read()[:settings.MAX_LOG_LENGTH]
        else:
            artifacts["console_logs"] = ""

        artifacts["screenshot_path"] = metadata.get("screenshot_file")
        return artifacts

    def _load_team_mapping(self) -> dict:
        try:
            with open(TEAM_MAPPING_PATH, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    # ── AI analysis ───────────────────────────────────────────────────

    def _ai_analyze(self, artifacts: dict) -> dict:
        user_prompt = build_analysis_prompt(
            test_name    = artifacts["test_name"],
            test_file    = artifacts["file"],
            duration     = artifacts["duration"],
            stacktrace   = artifacts["stacktrace"],
            console_logs = artifacts.get("console_logs", ""),
        )

        try:
            response = self.client.chat.completions.create(
                model       = settings.GROQ_MODEL,
                messages    = [
                    {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature = 0.1,
                max_tokens  = 1000,
            )

            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$",          "", raw).strip()
            return json.loads(raw)

        except json.JSONDecodeError as e:
            print(f"⚠️  Groq returned non-JSON: {e}")
            return self._fallback_analysis(artifacts)
        except Exception as e:
            print(f"⚠️  Groq API error: {e}")
            return self._fallback_analysis(artifacts)

    def _fallback_analysis(self, artifacts: dict) -> dict:
        lines     = artifacts.get("stacktrace", "").strip().split("\n")
        last_line = next(
            (l.strip() for l in reversed(lines) if l.strip() and not l.startswith(" ")),
            "Unknown error"
        )
        return {
            "error_type":         "Unknown",
            "error_message":      last_line[:200],
            "root_cause":         "Could not analyze — Groq API unavailable",
            "affected_component": "unknown",
            "affected_feature":   "Unknown feature",
            "user_impact":        "Unknown impact",
            "steps_to_reproduce": ["Run the failing test to reproduce"],
            "expected_behavior":  "Test should pass",
            "actual_behavior":    last_line[:200],
            "suggested_fix":      "Review the stack trace manually",
        }

    # ── Classification ────────────────────────────────────────────────

    def _classify(self, analysis: dict, stacktrace: str) -> tuple:
        error_type  = analysis.get("error_type",  "").lower()
        component   = analysis.get("affected_component", "").lower()
        user_impact = analysis.get("user_impact", "").lower()
        root_cause  = analysis.get("root_cause",  "").lower()

        CRITICAL_SIGNALS = [
            "payment", "checkout", "data loss", "security",
            "crash", "500", "internal server error", "keyerror",
        ]
        HIGH_SIGNALS = [
            "login", "nosuchelementexception", "assertionerror",
            "cannot", "not found", "not visible", "not displayed", "timeout",
        ]
        LOW_SIGNALS = [
            "typo", "colour", "color", "spacing", "alignment", "cosmetic",
        ]

        combined = f"{error_type} {component} {user_impact} {root_cause} {stacktrace.lower()}"
        severity  = "Medium"

        if any(s in combined for s in CRITICAL_SIGNALS):
            severity = "Critical"
        elif any(s in combined for s in HIGH_SIGNALS):
            severity = "High"
        elif any(s in combined for s in LOW_SIGNALS):
            severity = "Low"

        if component in ("login", "auth", "authentication") and severity == "Medium":
            severity = "High"

        priority_map = {"Critical": "P1", "High": "P2", "Medium": "P3", "Low": "P4"}
        return severity, priority_map[severity]

    # ── Assignee ──────────────────────────────────────────────────────

    def _get_assignee(self, component: str) -> dict:
        component = (component or "").lower().strip()
        return self.team_mapping.get(
            component,
            self.team_mapping.get("default", {"assignee": "", "accountId": "", "name": "Unassigned"})
        )

    # ── Report assembly ───────────────────────────────────────────────

    def _build_report(
        self,
        metadata:      dict,
        analysis:      dict,
        severity:      str,
        priority:      str,
        assignee_info: dict,
    ) -> dict:
        test_name = metadata["test_name"]
        component = analysis.get("affected_component", "unknown")
        feature   = analysis.get("affected_feature",   "Unknown feature")

        title = (
            f"[{component.upper()}] "
            f"{analysis.get('error_type', 'Test Failure')}: "
            f"{feature}"
        )

        steps_text = "\n".join(
            f"# {step}"
            for step in analysis.get("steps_to_reproduce", ["Run the failing test"])
        )

        description = f"""h2. 🤖 AI-Generated Bug Report

h3. Summary
{analysis.get('root_cause', 'See stack trace')}

h3. Steps to Reproduce
{steps_text}

h3. Expected Behavior
{analysis.get('expected_behavior', 'N/A')}

h3. Actual Behavior
{analysis.get('actual_behavior', 'N/A')}

h3. User Impact
{analysis.get('user_impact', 'N/A')}

h3. Suggested Fix
{analysis.get('suggested_fix', 'Review the stack trace')}

h3. Stack Trace
{{code}}
{metadata.get('failure_summary', '')[:800]}
{{code}}

h3. Test Details
* Test: {test_name}
* File: {metadata.get('file', 'N/A')}
* Duration: {metadata.get('duration', 0)}s
* Captured: {metadata.get('timestamp', 'N/A')}

---
_Generated automatically by Zero-Effort Bug Reporter_"""

        # Build labels including test dedup label
        test_label = _make_test_label(test_name)
        labels = ["ai-generated", "auto-reported", component, test_label]
        if analysis.get("error_type"):
            labels.append(analysis["error_type"].lower().replace("exception", ""))
        labels = list(dict.fromkeys(l.strip() for l in labels if l.strip()))

        return {
            "jira_project":        settings.JIRA_PROJECT_KEY,
            "issue_type":          "Bug",
            "title":               title,
            "description":         description,
            "severity":            severity,
            "priority":            priority,
            "assignee":            assignee_info.get("assignee", ""),
            "assignee_account_id": assignee_info.get("accountId", ""),
            "labels":              labels,
            "components":          [component],
            "attachments": {
                "screenshot":  metadata.get("screenshot_file"),
                "stacktrace":  metadata.get("stacktrace_file"),
            },
            "metadata": {
                "test_name":          test_name,
                "test_file":          metadata.get("file"),
                "timestamp":          metadata.get("timestamp"),
                "duration":           metadata.get("duration"),
                "groq_model":         settings.GROQ_MODEL,
                "affected_component": component,
                "error_type":         analysis.get("error_type"),
            },
        }

    def _save_report(self, bug_report: dict, test_name: str) -> str:
        out_dir = os.path.join(ARTIFACT_DIR, "bug_reports")
        os.makedirs(out_dir, exist_ok=True)

        safe = test_name
        for ch in " []/::.":
            safe = safe.replace(ch, "_")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path      = os.path.join(out_dir, f"bug_report_{safe}_{timestamp}.json")

        with open(path, "w", encoding="utf-8") as f:
            json.dump(bug_report, f, indent=2, ensure_ascii=False)

        return path


def main():
    if len(sys.argv) < 2:
        print("Usage: python agent/failure_agent.py <metadata_json_path>")
        sys.exit(1)

    metadata_path = sys.argv[1]
    if not os.path.exists(metadata_path):
        print(f"❌ File not found: {metadata_path}")
        sys.exit(1)

    agent  = FailureAnalysisAgent()
    result = agent.analyze_failure(metadata_path)
    report = result["bug_report"]

    print(f"\n{'='*60}")
    print(f"✅ BUG REPORT READY")
    print(f"{'='*60}")
    print(f"  Title    : {report['title']}")
    print(f"  Severity : {report['severity']}")
    print(f"  Priority : {report['priority']}")
    print(f"  Assignee : {report['assignee']}")
    print(f"  Labels   : {', '.join(report['labels'])}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()