"""
mcp_server/jira_tools.py
-------------------------
All direct Jira REST API calls.
"""

import base64
import os
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
    return httpx.Client(verify=False, timeout=30)


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
        "labels":      labels or ["ai-generated"],
    }

    if JIRA_SEVERITY_FIELD and severity:
        fields[JIRA_SEVERITY_FIELD] = {"value": severity}

    if components:
        fields["components"] = [{"name": c} for c in components]

    # Jira Cloud requires accountId, NOT emailAddress
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


def search_similar_bugs(project_key: str, search_text: str) -> dict:
    safe_text = search_text[:50].replace('"', '\\"')
    jql = (
        f'project = "{project_key}" '
        f'AND issuetype = Bug '
        f'AND status != Done '
        f'AND summary ~ "{safe_text}"'
    )
    params = {
        "jql":        jql,
        "maxResults": 5,
        "fields":     "summary,status,priority,assignee",
    }

    try:
        with _client() as client:
            response = client.get(
                _url("/search"),
                headers=HEADERS,
                params=params,
            )

        if response.status_code == 200:
            data   = response.json()
            issues = data.get("issues", [])
            return {
                "status": "success",
                "total":  data.get("total", 0),
                "issues": [
                    {
                        "key":     i["key"],
                        "summary": i["fields"]["summary"],
                        "status":  i["fields"]["status"]["name"],
                        "url":     f"{JIRA_BASE_URL}/browse/{i['key']}",
                    }
                    for i in issues
                ],
            }
        else:
            return {
                "status":  "error",
                "message": f"Search failed: {response.status_code}",
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
    if not file_path or not os.path.exists(file_path):
        return {"status": "skipped", "message": f"File not found: {file_path}"}

    attach_headers = {
        "Authorization":     HEADERS["Authorization"],
        "X-Atlassian-Token": "no-check",
    }

    try:
        with open(file_path, "rb") as f:
            file_content = f.read()

        filename = os.path.basename(file_path)

        with _client() as client:
            response = client.post(
                _url(f"/issue/{ticket_key}/attachments"),
                headers=attach_headers,
                files={"file": (filename, file_content)},
            )

        if response.status_code in (200, 201):
            return {"status": "success", "message": f"Attached {filename} to {ticket_key}"}
        else:
            return {
                "status":  "error",
                "message": f"Attachment failed: {response.status_code}",
                "details": response.text,
            }

    except Exception as e:
        return {"status": "error", "message": f"Attachment error: {str(e)}"}