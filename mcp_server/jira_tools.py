"""
mcp_server/jira_tools.py
-------------------------
All direct Jira REST API calls.
"""

import base64
import os
import platform
import httpx
from dotenv import load_dotenv

load_dotenv()

JIRA_BASE_URL       = os.getenv("JIRA_BASE_URL", "").rstrip("/")
JIRA_EMAIL          = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN      = os.getenv("JIRA_API_TOKEN", "")
JIRA_SEVERITY_FIELD = os.getenv("JIRA_SEVERITY_FIELD", "")

_credentials = base64.b64encode(
    f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()
).decode()

HEADERS = {
    "Authorization": f"Basic {_credentials}",
    "Content-Type":  "application/json",
    "Accept":        "application/json",
}


def _url(path: str) -> str:
    return f"{JIRA_BASE_URL}/rest/api/2{path}"


def _client() -> httpx.Client:
    verify = platform.system() != "Windows"
    return httpx.Client(verify=verify, timeout=30)


def _make_test_label(test_name: str) -> str:
    """
    Convert pytest node ID to a short Jira-safe label.
    e.g. tests/test_login.py::TestLoginFailurePaths::test_login_with_invalid_password
    →    test-login_with_invalid_password
    """
    import re
    parts = test_name.split("::")
    name  = parts[-1] if parts else test_name
    name  = re.sub(r"_\d{8}_\d{6,9}$", "", name)
    safe  = name.lower()
    for ch in " /\\.[]():":
        safe = safe.replace(ch, "_")
    safe = re.sub(r"_+", "_", safe).strip("_")
    return f"test-{safe}"[:100]


def create_bug(
    project_key:  str,
    title:        str,
    description:  str,
    severity:     str,
    priority:     str,
    assignee:     str = None,
    labels:       list = None,
    components:   list = None,
) -> dict:
    priority_map = {
        "P1": "Highest",
        "P2": "High",
        "P3": "Medium",
        "P4": "Low",
    }
    jira_priority = priority_map.get(priority, "Medium")

    fields = {
        "project":     {"key": project_key},
        "summary":     title,
        "description": description,
        "issuetype":   {"name": "Bug"},
        "priority":    {"name": jira_priority},
        "labels":      [l for l in (labels or ["ai-generated"]) if l and l.strip()],
    }

    # NEW — strip whitespace before checking
    if JIRA_SEVERITY_FIELD and JIRA_SEVERITY_FIELD.strip() and severity:
        fields[JIRA_SEVERITY_FIELD.strip()] = {"value": severity}

    # Filter out blank component names before sending to Jira
    

    if assignee:
        fields["assignee"] = {"accountId": assignee}

    payload = {"fields": fields}

    try:
        with _client() as client:
            response = client.post(
                _url("/issue"),
                headers=HEADERS,
                json=payload,
            )

        if response.status_code in (200, 201):
            data       = response.json()
            ticket_key = data.get("key", "UNKNOWN")
            ticket_url = f"{JIRA_BASE_URL}/browse/{ticket_key}"
            return {
                "status":     "success",
                "ticket_key": ticket_key,
                "ticket_url": ticket_url,
                "ticket_id":  data.get("id"),
                "message":    f"Bug ticket {ticket_key} created successfully",
            }
        else:
            return {
                "status":  "error",
                "code":    response.status_code,
                "message": f"Jira API error {response.status_code}",
                "details": response.text,
            }

    except Exception as e:
        return {"status": "error", "message": f"Connection error: {str(e)}"}


def search_similar_bugs(project_key: str, test_name: str, component: str) -> dict:
    safe_label = _make_test_label(test_name)
    print(f"🔑 Dedup label: {safe_label}")

    jql_label = (
        f'project = "{project_key}" '
        f'AND issuetype = Bug '
        f'AND status != Done '
        f'AND labels = "{safe_label}"'
    )

    # ── DEBUG: print exact JQL being sent ────────────────────────────
    print(f"   JQL: {jql_label}")

    try:
        with _client() as client:
            r = client.get(
                _url("/search"),
                headers=HEADERS,
                params={
                    "jql":        jql_label,
                    "maxResults": 5,
                    "fields":     "summary,status,priority,labels",
                },
            )

        # ── DEBUG: print raw response ─────────────────────────────────
        print(f"   Search status: {r.status_code}")
        if r.status_code == 200:
            total = r.json().get("total", 0)
            print(f"   Search total:  {total}")
            issues = r.json().get("issues", [])
            if issues:
                print(f"   Found labels on first result: {issues[0]['fields'].get('labels', [])}")
            if issues:
                return {
                    "status":       "success",
                    "total":        len(issues),
                    "match_method": "test_label",
                    "issues": [
                        {
                            "key":      i["key"],
                            "summary":  i["fields"]["summary"],
                            "status":   i["fields"]["status"]["name"],
                            "priority": i["fields"]["priority"]["name"],
                            "url":      f"{JIRA_BASE_URL}/browse/{i['key']}",
                        }
                        for i in issues
                    ],
                }
        else:
            print(f"   Search error: {r.text[:200]}")
    except Exception as e:
        print(f"⚠️  Label search error: {e}")

    # Strategy 2 — component label fallback
    jql_component = (
        f'project = "{project_key}" '
        f'AND issuetype = Bug '
        f'AND status != Done '
        f'AND labels = "auto-reported" '
        f'AND labels = "{component}"'
    )

    print(f"   Fallback JQL: {jql_component}")

    try:
        with _client() as client:
            r = client.get(
                _url("/search"),
                headers=HEADERS,
                params={
                    "jql":        jql_component,
                    "maxResults": 5,
                    "fields":     "summary,status,priority,labels",
                },
            )

        print(f"   Fallback status: {r.status_code}, total: {r.json().get('total', 0) if r.status_code == 200 else 'N/A'}")

        if r.status_code == 200:
            issues = r.json().get("issues", [])
            return {
                "status":       "success",
                "total":        len(issues),
                "match_method": "component_label",
                "issues": [
                    {
                        "key":      i["key"],
                        "summary":  i["fields"]["summary"],
                        "status":   i["fields"]["status"]["name"],
                        "priority": i["fields"]["priority"]["name"],
                        "url":      f"{JIRA_BASE_URL}/browse/{i['key']}",
                    }
                    for i in issues
                ],
            }
        else:
            return {
                "status":  "error",
                "total":   0,
                "issues":  [],
                "message": f"Search failed: {r.status_code}",
            }

    except Exception as e:
        return {"status": "error", "total": 0, "issues": [], "message": str(e)}

def escalate_priority(ticket_key: str, current_priority: str) -> dict:
    escalation_map = {
        "Low":     "Medium",
        "Medium":  "High",
        "High":    "Highest",
        "Highest": "Highest",
    }
    new_priority = escalation_map.get(current_priority, "High")

    if new_priority == current_priority:
        return {
            "status":       "skipped",
            "message":      f"{ticket_key} is already at Highest priority — no escalation needed",
            "new_priority": new_priority,
        }

    payload = {"fields": {"priority": {"name": new_priority}}}

    try:
        with _client() as client:
            response = client.put(
                _url(f"/issue/{ticket_key}"),
                headers=HEADERS,
                json=payload,
            )

        if response.status_code in (200, 201, 204):
            return {
                "status":       "success",
                "message":      f"Priority escalated: {current_priority} → {new_priority}",
                "old_priority": current_priority,
                "new_priority": new_priority,
            }
        else:
            return {
                "status":  "error",
                "message": f"Priority update failed: {response.status_code}",
                "details": response.text,
            }

    except Exception as e:
        return {"status": "error", "message": f"Connection error: {str(e)}"}


def add_comment(ticket_key: str, comment: str) -> dict:
    payload = {"body": comment}

    try:
        with _client() as client:
            response = client.post(
                _url(f"/issue/{ticket_key}/comment"),
                headers=HEADERS,
                json=payload,
            )

        if response.status_code in (200, 201):
            return {"status": "success", "message": f"Comment added to {ticket_key}"}
        else:
            return {
                "status":  "error",
                "message": f"Failed to add comment: {response.status_code}",
                "details": response.text,
            }

    except Exception as e:
        return {"status": "error", "message": f"Connection error: {str(e)}"}


def attach_file(ticket_key: str, file_path: str) -> dict:
    """
    Attach file to Jira ticket.
    If Zscaler blocks it (403), skip silently — no base64 fallback.
    """
    if not file_path or not os.path.exists(file_path):
        return {"status": "skipped", "message": f"File not found: {file_path}"}

    attach_headers = {
        "Authorization":     HEADERS["Authorization"],
        "X-Atlassian-Token": "no-check",
    }

    try:
        filename = os.path.basename(file_path)

        with open(file_path, "rb") as f:
            file_content = f.read()

        with _client() as client:
            response = client.post(
                _url(f"/issue/{ticket_key}/attachments"),
                headers=attach_headers,
                files={"file": (filename, file_content)},
            )

        if response.status_code in (200, 201):
            return {
                "status":  "success",
                "message": f"Attached {filename} to {ticket_key}",
            }
        elif response.status_code == 403:
            return {
                "status":  "skipped",
                "message": f"Attachment blocked by proxy (403) — skipping {filename}",
            }
        else:
            return {
                "status":  "error",
                "message": f"Attachment failed: {response.status_code}",
                "details": response.text,
            }

    except Exception as e:
        return {"status": "error", "message": f"Attachment error: {str(e)}"}
    
def get_ticket(ticket_key: str) -> dict:
    """Fetch a single ticket's current priority and URL."""
    try:
        with _client() as client:
            r = client.get(
                _url(f"/issue/{ticket_key}"),
                headers=HEADERS,
                params={"fields": "priority,status,summary"},
            )
        if r.status_code == 200:
            data = r.json()
            return {
                "priority": data["fields"]["priority"]["name"],
                "status":   data["fields"]["status"]["name"],
                "summary":  data["fields"]["summary"],
                "url":      f"{JIRA_BASE_URL}/browse/{ticket_key}",
            }
        else:
            return {"priority": "Medium", "url": f"{JIRA_BASE_URL}/browse/{ticket_key}"}
    except Exception:
        return {"priority": "Medium", "url": f"{JIRA_BASE_URL}/browse/{ticket_key}"}    