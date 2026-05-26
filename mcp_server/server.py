"""
mcp_server/server.py
---------------------
The MCP Server — exposes Jira tools to the AI agent over stdio transport.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
from mcp.server.fastmcp import FastMCP
from mcp_server.jira_tools import (
    create_bug,
    search_similar_bugs,
    add_comment,
    attach_file,
)

mcp = FastMCP("jira-bug-reporter")


@mcp.tool()
def jira_create_bug(
    project_key:  str,
    title:        str,
    description:  str,
    severity:     str,
    priority:     str,
    assignee:     str = "",
    labels:       str = "[]",
    components:   str = "[]",
) -> str:
    """
    Create a new Bug ticket in Jira.

    Args:
        project_key : Jira project key e.g. AUTO
        title       : Bug summary / title
        description : Full description in Jira wiki markup
        severity    : Critical | High | Medium | Low
        priority    : P1 | P2 | P3 | P4
        assignee    : Assignee accountId (not email)
        labels      : JSON array string e.g. '["login","ui-bug"]'
        components  : JSON array string e.g. '["login"]'

    Returns:
        JSON string with ticket_key, ticket_url, status
    """
    try:
        labels_list     = json.loads(labels)     if labels     else []
        components_list = json.loads(components) if components else []
    except json.JSONDecodeError:
        labels_list     = []
        components_list = []

    result = create_bug(
        project_key  = project_key,
        title        = title,
        description  = description,
        severity     = severity,
        priority     = priority,
        assignee     = assignee or None,
        labels       = labels_list,
        components   = components_list,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def jira_search_similar_bugs(project_key: str, search_text: str) -> str:
    """
    Search for open bugs with a similar title. Use BEFORE creating
    a new bug to check for duplicates.

    Args:
        project_key : Jira project key
        search_text : Text to search for in bug summaries

    Returns:
        JSON string with list of matching issues
    """
    result = search_similar_bugs(project_key, search_text)
    return json.dumps(result, indent=2)


@mcp.tool()
def jira_add_comment(ticket_key: str, comment: str) -> str:
    """
    Add a comment to an existing Jira ticket.
    Use when a duplicate is found instead of creating a new ticket.

    Args:
        ticket_key : e.g. AUTO-123
        comment    : Comment text (Jira wiki markup supported)

    Returns:
        JSON string with status
    """
    result = add_comment(ticket_key, comment)
    return json.dumps(result, indent=2)


@mcp.tool()
def jira_attach_file(ticket_key: str, file_path: str) -> str:
    """
    Attach a screenshot or log file to a Jira ticket.

    Args:
        ticket_key : e.g. AUTO-123
        file_path  : Absolute path to the file on disk

    Returns:
        JSON string with status
    """
    result = attach_file(ticket_key, file_path)
    return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")