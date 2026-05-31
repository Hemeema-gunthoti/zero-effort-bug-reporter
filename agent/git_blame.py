"""
agent/git_blame.py
-------------------
Runs git blame on the failing test file to find who last
changed the relevant line and returns their name + email.
"""

import os
import re
import subprocess


def get_blame_assignee(test_file: str, stacktrace: str) -> dict:
    """
    1. Extract failing line number from stack trace
    2. Run git blame on that line
    3. Return author name + email
    """
    line_number = _extract_line_number(test_file, stacktrace)

    if not line_number:
        return {"found": False, "reason": "Could not extract line number from stack trace"}

    return _git_blame(test_file, line_number)


def get_last_pusher() -> dict:
    """
    Fallback — get author of the most recent git commit.
    Also reads GITHUB_ACTOR env var when running in GitHub Actions.
    """
    try:
        github_actor = os.getenv("GITHUB_ACTOR", "")
        github_email = os.getenv("GIT_AUTHOR_EMAIL", "")

        if github_actor:
            return {
                "found":  True,
                "name":   github_actor,
                "email":  github_email or f"{github_actor}@users.noreply.github.com",
                "source": "github_actions",
            }

        result = subprocess.run(
            ["git", "log", "-1", "--format=%an|%ae"],
            capture_output = True,
            text           = True,
            timeout        = 10,
        )

        if result.returncode == 0 and "|" in result.stdout:
            name, email = result.stdout.strip().split("|", 1)
            return {
                "found":  True,
                "name":   name.strip(),
                "email":  email.strip(),
                "source": "git_log",
            }

        return {"found": False, "reason": "git log failed"}

    except Exception as e:
        return {"found": False, "reason": str(e)}


def _extract_line_number(test_file: str, stacktrace: str) -> int | None:
    """
    Extract failing line number from pytest stack trace.
    Handles both formats:
      test_login.py:72: in test_login_with_invalid_password
      File "tests/test_login.py", line 72
    """
    filename = os.path.basename(test_file)

    # Pattern 1 — pytest short: test_login.py:72:
    match = re.search(rf"{re.escape(filename)}:(\d+)", stacktrace)
    if match:
        return int(match.group(1))

    # Pattern 2 — full traceback: line 72
    match = re.search(
        rf'["\']?{re.escape(filename)}["\']?,\s*line\s+(\d+)',
        stacktrace,
        re.IGNORECASE,
    )
    if match:
        return int(match.group(1))

    return None


def _git_blame(file_path: str, line_number: int) -> dict:
    """
    Run git blame -L <line>,<line> --porcelain on the file.
    Returns the author name and email of whoever last touched that line.
    """
    try:
        result = subprocess.run(
            [
                "git", "blame",
                "-L", f"{line_number},{line_number}",
                "--porcelain",
                file_path,
            ],
            capture_output = True,
            text           = True,
            timeout        = 10,
        )

        if result.returncode != 0:
            return {
                "found":  False,
                "reason": f"git blame failed: {result.stderr.strip()}",
            }

        output = result.stdout

        name_match  = re.search(r"^author (.+)$",        output, re.MULTILINE)
        email_match = re.search(r"^author-mail <(.+)>$", output, re.MULTILINE)

        if not name_match:
            return {"found": False, "reason": "Could not parse git blame output"}

        name  = name_match.group(1).strip()
        email = email_match.group(1).strip() if email_match else ""

        if "not committed" in name.lower():
            return {
                "found":  False,
                "reason": "Line has uncommitted changes — no blame available",
            }

        print(f"   🔍 Git blame: line {line_number} last changed by {name} <{email}>")

        return {
            "found": True,
            "name":  name,
            "email": email,
            "line":  line_number,
        }

    except FileNotFoundError:
        return {"found": False, "reason": "git not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"found": False, "reason": "git blame timed out"}
    except Exception as e:
        return {"found": False, "reason": str(e)}