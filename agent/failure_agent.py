"""
agent/failure_agent.py
-----------------------
The core AI agent.

Entry point: FailureAnalysisAgent.analyze_failure(metadata_path)

  1. Reads the failure metadata JSON (written by conftest.py)
  2. Loads stack trace and console log files from disk
  3. Sends everything to Groq for analysis
  4. Classifies severity and priority (hybrid: rules + AI)
  5. Determines the assignee from team_mapping.json
  6. Writes the final bug_report.json to artifacts/bug_reports/
  7. Returns the bug report dict for the next pipeline step (MCP → Jira)

GROQ NOTE:
  Groq's Python SDK is OpenAI-compatible.
  The only differences from OpenAI are:
    - from groq import Groq  (not openai)
    - client = Groq(api_key=...)
  Everything else — messages, response structure — is identical.
"""

import json
import os
import re
import sys
from datetime import datetime

from groq import Groq

from config.settings import settings
from agent.prompts.analysis_prompt import (
    ANALYSIS_SYSTEM_PROMPT,
    build_analysis_prompt,
)

# Path constants
ARTIFACT_DIR     = os.path.join(os.path.dirname(__file__), "..", "artifacts")
TEAM_MAPPING_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "team_mapping.json")


class FailureAnalysisAgent:
    """
    Analyzes a single test failure end-to-end and produces
    a structured bug_report.json ready to be sent to Jira.
    """

    def __init__(self):
        self.client = Groq(api_key=settings.GROQ_API_KEY)
        self.team_mapping = self._load_team_mapping()

    # ──────────────────────────────────────────────────────────────────
    # PUBLIC
    # ──────────────────────────────────────────────────────────────────

    def analyze_failure(self, metadata_path: str) -> dict:
        """
        Full pipeline for one test failure.
        Returns the complete bug report dict.
        """
        print(f"\n{'='*60}")
        print(f"🤖 AI AGENT STARTING")
        print(f"   Input: {metadata_path}")
        print(f"{'='*60}")

        # ── Step 1: Load metadata and artifact files ──────────────────
        metadata  = self._load_metadata(metadata_path)
        artifacts = self._load_artifacts(metadata)

        print(f"✅ Artifacts loaded")
        print(f"   Test : {metadata['test_name']}")
        print(f"   Stack: {len(artifacts['stacktrace'])} chars")
        print(f"   Logs : {len(artifacts.get('console_logs', ''))} chars")

        # ── Step 2: AI analysis via Groq ──────────────────────────────
        print(f"\n🧠 Sending to Groq ({settings.GROQ_MODEL})...")
        analysis = self._ai_analyze(artifacts)
        print(f"✅ Groq analysis complete")
        print(f"   Error type       : {analysis.get('error_type')}")
        print(f"   Affected component: {analysis.get('affected_component')}")
        print(f"   Root cause       : {analysis.get('root_cause', '')[:80]}...")

        # ── Step 3: Classify severity and priority ────────────────────
        severity, priority = self._classify(analysis, artifacts["stacktrace"])
        print(f"\n📊 Classification: severity={severity}  priority={priority}")

        # ── Step 4: Determine assignee ────────────────────────────────
        assignee_info = self._get_assignee(analysis.get("affected_component", "default"))
        print(f"👤 Assignee: {assignee_info['name']} ({assignee_info['assignee']})")

        # ── Step 5: Build the final bug report ────────────────────────
        bug_report = self._build_report(
            metadata, analysis, severity, priority, assignee_info
        )

        # ── Step 6: Save to disk ──────────────────────────────────────
        output_path = self._save_report(bug_report, metadata["test_name"])
        print(f"\n💾 Bug report saved → {output_path}")

        return {
            "bug_report":  bug_report,
            "output_path": output_path,
            "metadata":    metadata,
        }

    # ──────────────────────────────────────────────────────────────────
    # PRIVATE — Data loading
    # ──────────────────────────────────────────────────────────────────

    def _load_metadata(self, path: str) -> dict:
        """Read the failure_*.json file written by conftest.py."""
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _load_artifacts(self, metadata: dict) -> dict:
        """
        Load the actual content of each artifact file.
        Truncates long files so we don't blow the Groq context window.
        """
        artifacts = {
            "test_name":  metadata["test_name"],
            "file":       metadata["file"],
            "duration":   metadata.get("duration", 0),
            "timestamp":  metadata["timestamp"],
        }

        # Stack trace — always present
        trace_path = metadata.get("stacktrace_file")
        if trace_path and os.path.exists(trace_path):
            with open(trace_path, encoding="utf-8") as f:
                content = f.read()
            # Truncate to avoid token limit issues
            artifacts["stacktrace"] = content[:settings.MAX_STACKTRACE_LENGTH]
        else:
            artifacts["stacktrace"] = metadata.get("failure_summary", "No stack trace available.")

        # Console logs — optional (only Selenium tests have these)
        log_path = metadata.get("console_log_file")
        if log_path and os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as f:
                content = f.read()
            artifacts["console_logs"] = content[:settings.MAX_LOG_LENGTH]
        else:
            artifacts["console_logs"] = ""

        # Screenshot path — we don't send the image to Groq (text model)
        # but we include the path in the bug report for Jira attachment
        artifacts["screenshot_path"] = metadata.get("screenshot_file")

        return artifacts

    def _load_team_mapping(self) -> dict:
        """Load the component → assignee mapping from config."""
        try:
            with open(TEAM_MAPPING_PATH, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"⚠️  team_mapping.json not found — using empty mapping")
            return {}

    # ──────────────────────────────────────────────────────────────────
    # PRIVATE — AI analysis
    # ──────────────────────────────────────────────────────────────────

    def _ai_analyze(self, artifacts: dict) -> dict:
        """
        Send the failure data to Groq and parse the JSON response.

        We use a strict system prompt that tells the model to respond
        with raw JSON only. We also strip markdown fences defensively
        in case the model adds them anyway.
        """
        user_prompt = build_analysis_prompt(
            test_name   = artifacts["test_name"],
            test_file   = artifacts["file"],
            duration    = artifacts["duration"],
            stacktrace  = artifacts["stacktrace"],
            console_logs= artifacts.get("console_logs", ""),
        )

        try:
            response = self.client.chat.completions.create(
                model    = settings.GROQ_MODEL,
                messages = [
                    {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature = 0.1,   # Low temperature = consistent, factual output
                max_tokens  = 1000,
            )

            raw = response.choices[0].message.content.strip()

            # Defensively strip markdown code fences if model adds them
            # e.g. ```json { ... } ``` → { ... }
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$",          "", raw)
            raw = raw.strip()

            analysis = json.loads(raw)
            return analysis

        except json.JSONDecodeError as e:
            print(f"⚠️  Groq returned non-JSON response: {e}")
            print(f"   Raw response: {raw[:200]}")
            # Return a safe fallback so the pipeline doesn't crash
            return self._fallback_analysis(artifacts)

        except Exception as e:
            print(f"⚠️  Groq API error: {e}")
            return self._fallback_analysis(artifacts)

    def _fallback_analysis(self, artifacts: dict) -> dict:
        """
        Used when Groq fails or returns bad JSON.
        Extracts basic info from the stack trace using simple string parsing
        so the pipeline can still create a (less detailed) Jira ticket.
        """
        stacktrace = artifacts.get("stacktrace", "")

        # Try to extract the last error line from the stack trace
        lines      = stacktrace.strip().split("\n")
        last_line  = next(
            (l.strip() for l in reversed(lines) if l.strip() and not l.startswith(" ")),
            "Unknown error"
        )

        return {
            "error_type":          "Unknown",
            "error_message":       last_line[:200],
            "root_cause":          "Could not analyze — Groq API unavailable",
            "affected_component":  "unknown",
            "affected_feature":    "Unknown feature",
            "user_impact":         "Unknown impact",
            "steps_to_reproduce":  ["Run the failing test to reproduce"],
            "expected_behavior":   "Test should pass",
            "actual_behavior":     last_line[:200],
            "suggested_fix":       "Review the stack trace manually",
        }

    # ──────────────────────────────────────────────────────────────────
    # PRIVATE — Classification
    # ──────────────────────────────────────────────────────────────────

    def _classify(self, analysis: dict, stacktrace: str) -> tuple[str, str]:
        """
        Hybrid severity + priority classification.

        Rules run first (fast, deterministic).
        AI suggestion is used as a tiebreaker or override for edge cases.

        Returns: (severity, priority)
          severity → Critical | High | Medium | Low
          priority → P1 | P2 | P3 | P4
        """
        error_type  = analysis.get("error_type",  "").lower()
        component   = analysis.get("affected_component", "").lower()
        user_impact = analysis.get("user_impact", "").lower()
        root_cause  = analysis.get("root_cause",  "").lower()

        # ── Rule-based severity ───────────────────────────────────────
        severity = "Medium"   # default

        CRITICAL_SIGNALS = [
            "payment", "checkout", "data loss", "security",
            "crash", "500", "internal server error", "keyerror",
            "authentication bypass",
        ]
        HIGH_SIGNALS = [
            "login", "nosuchelementexception", "assertionerror",
            "cannot", "not found", "not visible", "not displayed",
            "timeout",
        ]
        LOW_SIGNALS = [
            "typo", "colour", "color", "spacing", "alignment",
            "cosmetic", "style",
        ]

        combined = f"{error_type} {component} {user_impact} {root_cause} {stacktrace.lower()}"

        if any(s in combined for s in CRITICAL_SIGNALS):
            severity = "Critical"
        elif any(s in combined for s in HIGH_SIGNALS):
            severity = "High"
        elif any(s in combined for s in LOW_SIGNALS):
            severity = "Low"

        # ── Rule-based priority (follows from severity) ───────────────
        priority_map = {
            "Critical": "P1",
            "High":     "P2",
            "Medium":   "P3",
            "Low":      "P4",
        }
        priority = priority_map[severity]

        # ── Escalation: login + auth issues are always at least High ──
        if component in ("login", "auth", "authentication") and severity == "Medium":
            severity = "High"
            priority = "P2"

        return severity, priority

    # ──────────────────────────────────────────────────────────────────
    # PRIVATE — Assignee
    # ──────────────────────────────────────────────────────────────────

    def _get_assignee(self, component: str) -> dict:
        """
        Look up the team member responsible for a component.
        Falls back to 'default' if the component is not in the mapping.
        """
        component = (component or "").lower().strip()
        return self.team_mapping.get(
            component,
            self.team_mapping.get("default", {
                "assignee": "",
                "name": "Unassigned",
            })
        )

    # ──────────────────────────────────────────────────────────────────
    # PRIVATE — Report assembly
    # ──────────────────────────────────────────────────────────────────

    def _build_report(
        self,
        metadata:      dict,
        analysis:      dict,
        severity:      str,
        priority:      str,
        assignee_info: dict,
    ) -> dict:
        """
        Assemble the final bug report JSON.
        This is exactly what gets sent to Jira in Layer 4.

        The description field uses Jira wiki markup so it renders
        nicely in the ticket view.
        """
        test_name  = metadata["test_name"]
        component  = analysis.get("affected_component", "unknown")
        feature    = analysis.get("affected_feature",   "Unknown feature")

        # ── Title ─────────────────────────────────────────────────────
        title = (
            f"[{component.upper()}] "
            f"{analysis.get('error_type', 'Test Failure')}: "
            f"{feature}"
        )

        # ── Description in Jira wiki markup ───────────────────────────
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

        # ── Labels ────────────────────────────────────────────────────
        labels = ["ai-generated", "auto-reported", component]
        if analysis.get("error_type"):
            labels.append(analysis["error_type"].lower().replace("exception", ""))
        # Deduplicate and clean
        labels = list(dict.fromkeys(l.strip() for l in labels if l.strip()))

        return {
            "jira_project":  settings.JIRA_PROJECT_KEY,
            "issue_type":    "Bug",
            "title":         title,
            "description":   description,
            "severity":      severity,
            "priority":      priority,
            "assignee":     assignee_info.get("assignee", ""),
            "assignee_account_id": assignee_info.get("accountId", ""),
            "labels":        labels,
            "components":    [component],
            "attachments": {
                "screenshot":  metadata.get("screenshot_file"),
                "stacktrace":  metadata.get("stacktrace_file"),
            },
            "metadata": {
                "test_name":             test_name,
                "test_file":             metadata.get("file"),
                "timestamp":             metadata.get("timestamp"),
                "duration":              metadata.get("duration"),
                "groq_model":            settings.GROQ_MODEL,
                "affected_component":    component,
                "error_type":            analysis.get("error_type"),
            },
        }

    # ──────────────────────────────────────────────────────────────────
    # PRIVATE — Save
    # ──────────────────────────────────────────────────────────────────

    def _save_report(self, bug_report: dict, test_name: str) -> str:
        """Write bug_report.json to artifacts/bug_reports/."""
        out_dir = os.path.join(ARTIFACT_DIR, "bug_reports")
        os.makedirs(out_dir, exist_ok=True)

        safe = test_name
        for ch in " []/::.":
            safe = safe.replace(ch, "_")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(out_dir, f"bug_report_{safe}_{timestamp}.json")

        with open(path, "w", encoding="utf-8") as f:
            json.dump(bug_report, f, indent=2, ensure_ascii=False)

        return path


# ──────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────

def main():
    """
    Called directly by the CI pipeline or manually:
      python agent/failure_agent.py artifacts/failure_xyz.json
    """
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
    print(f"  Saved to : {result['output_path']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()