"""
webhook_handler.py
FastAPI server that receives clicks from the email's CTA buttons
and dispatches the ai-fix workflow via GitHub API.

Deploy this on Render / Railway / any server.
Set WEBHOOK_BASE_URL secret in GitHub to point here.

Endpoints:
  GET /trigger-ai-fix   → triggered by "Fix with AI" email button
  GET /manual-ack       → triggered by "Fix Manually" button (just logs)
"""

import os
import httpx
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse

app = FastAPI(title="Zero-Effort Bug Reporter — Webhook Handler")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")   # Fine-grained PAT with actions:write
REPO         = os.environ.get("DEFAULT_REPO", "")   # fallback if not passed in query


# ───────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────

def _dispatch_ai_fix(repo: str, branch: str, commit_sha: str, run_id: str) -> dict:
    """
    Calls GitHub Actions workflow_dispatch to trigger ai-fix.yml
    """
    url = f"https://api.github.com/repos/{repo}/actions/workflows/ai-fix.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "ref": branch,
        "inputs": {
            "branch":     branch,
            "commit_sha": commit_sha,
            "run_id":     run_id,
        },
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=15)
    if resp.status_code not in (204, 200):
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"GitHub dispatch failed: {resp.text}",
        )
    return {"status": "dispatched", "workflow": "ai-fix.yml", "branch": branch}


def _success_page(title: str, message: str, color: str = "#6366f1") -> HTMLResponse:
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>{title}</title>
      <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
          background: #0f1117;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          display: flex; align-items: center; justify-content: center;
          min-height: 100vh; padding: 24px;
        }}
        .card {{
          background: #1a1d27;
          border: 1px solid #2d3148;
          border-radius: 16px;
          padding: 48px 40px;
          max-width: 520px;
          width: 100%;
          text-align: center;
          box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        }}
        .icon {{ font-size: 56px; margin-bottom: 20px; }}
        h1 {{ color: #e0e7ff; font-size: 24px; margin-bottom: 12px; }}
        p  {{ color: #9ca3af; font-size: 15px; line-height: 1.6; }}
        .badge {{
          display: inline-block;
          margin-top: 24px;
          padding: 10px 24px;
          background: {color};
          color: #fff;
          border-radius: 8px;
          font-size: 14px;
          font-weight: 700;
          text-decoration: none;
        }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="icon">🤖</div>
        <h1>{title}</h1>
        <p>{message}</p>
        <span class="badge">Action Triggered</span>
      </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


# ───────────────────────────────────────────────
# Routes
# ───────────────────────────────────────────────

@app.get("/trigger-ai-fix", response_class=HTMLResponse)
async def trigger_ai_fix(
    repo:       str = Query(default=None),
    branch:     str = Query(default="main"),
    commit_sha: str = Query(default=""),
    run_id:     str = Query(default=""),
):
    """
    Called when user clicks "Fix with AI" in the bug report email.
    Dispatches the ai-fix.yml workflow and shows a confirmation page.
    """
    effective_repo = repo or REPO
    if not effective_repo:
        raise HTTPException(status_code=400, detail="repo parameter required")
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="GITHUB_TOKEN not configured on server")

    _dispatch_ai_fix(effective_repo, branch, commit_sha, run_id)

    return _success_page(
        title="AI Fix Triggered",
        message=(
            f"The AI agent has been dispatched on branch <strong>{branch}</strong>. "
            "It will analyze the failing tests, apply patches, re-run the full "
            "test suite, and merge into <strong>main</strong> only if all tests pass. "
            "You'll receive another email with the result."
        ),
        color="#6366f1",
    )


@app.get("/manual-ack", response_class=HTMLResponse)
async def manual_ack(
    repo:   str = Query(default=None),
    run_id: str = Query(default=""),
):
    """
    Called when user clicks "Fix Manually".
    Just shows a confirmation page — no action taken.
    """
    effective_repo = repo or REPO
    run_url = f"https://github.com/{effective_repo}/actions/runs/{run_id}" if run_id else "#"

    html = f"""
    <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Manual Fix Acknowledged</title>
    <style>
      * {{ box-sizing:border-box; margin:0; padding:0; }}
      body {{ background:#0f1117; font-family:-apple-system,sans-serif;
              display:flex; align-items:center; justify-content:center;
              min-height:100vh; padding:24px; }}
      .card {{ background:#1a1d27; border:1px solid #2d3148; border-radius:16px;
               padding:48px 40px; max-width:520px; width:100%; text-align:center;
               box-shadow:0 8px 32px rgba(0,0,0,.4); }}
      h1 {{ color:#e0e7ff; font-size:24px; margin-bottom:12px; }}
      p  {{ color:#9ca3af; font-size:15px; line-height:1.6; }}
      a.btn {{ display:inline-block; margin-top:24px; padding:10px 24px;
               background:#374151; color:#fff; border-radius:8px;
               font-size:14px; font-weight:700; text-decoration:none; }}
    </style></head>
    <body><div class="card">
      <div style="font-size:56px;margin-bottom:20px;">🔧</div>
      <h1>Manual Fix Selected</h1>
      <p>Head over to the GitHub Actions run to investigate and push your own fix.</p>
      <a class="btn" href="{run_url}" target="_blank">Open GitHub Actions Run →</a>
    </div></body></html>
    """
    return HTMLResponse(content=html)


@app.get("/health")
async def health():
    return {"status": "ok"}