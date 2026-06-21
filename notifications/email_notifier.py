"""
notifications/email_notifier.py
--------------------------------
Sends email notifications when:
  - A new bug ticket is created
  - A duplicate is found and priority is escalated
  - AI fix is proposed (with approve/reject buttons in the email)

Uses Python's built-in smtplib — no extra dependencies needed.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from datetime             import datetime
from dotenv               import load_dotenv

load_dotenv()

SMTP_SERVER   = os.getenv("SMTP_SERVER",   "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
FROM_EMAIL    = os.getenv("FROM_EMAIL",    "")


def notify_email(bug_report: dict, jira_result: dict) -> dict:
    if not _is_configured():
        print(f"📧 Email skipped — SMTP not configured in .env")
        return {"status": "skipped", "message": "SMTP not configured"}

    status   = jira_result.get("status", "unknown")
    to_email = SMTP_USERNAME

    try:
        if status == "success":
            return _send_new_ticket_email(bug_report, jira_result, to_email)
        elif status == "duplicate":
            return _send_escalation_email(bug_report, jira_result, to_email)
        else:
            print(f"📧 Email skipped — unhandled status: {status}")
            return {"status": "skipped", "message": f"Unhandled status: {status}"}
    except Exception as e:
        print(f"📧 Email failed: {e}")
        return {"status": "error", "message": str(e)}


def _send_new_ticket_email(bug_report, jira_result, to_email):
    ticket_key = jira_result.get("ticket_key", "N/A")
    ticket_url = jira_result.get("ticket_url", "")
    severity   = bug_report.get("severity", "Unknown")
    priority   = bug_report.get("priority", "Unknown")
    title      = bug_report.get("title", "Unknown")
    test_name  = bug_report["metadata"].get("test_name", "Unknown")
    component  = bug_report["metadata"].get("affected_component", "Unknown")
    timestamp  = bug_report["metadata"].get("timestamp", "Unknown")

    subject = f"🐛 [{severity}] New Bug: {ticket_key} — {title[:60]}"

    html = f"""
<!DOCTYPE html><html><head><style>
body{{font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px;}}
.container{{max-width:600px;margin:auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);}}
.header{{background:{'#dc2626' if severity=='Critical' else '#d97706' if severity=='High' else '#2563eb'};color:#fff;padding:24px 32px;}}
.header h1{{margin:0;font-size:20px;}}.header p{{margin:6px 0 0;opacity:0.9;font-size:14px;}}
.body{{padding:24px 32px;}}
table{{width:100%;border-collapse:collapse;margin-top:16px;}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:14px;}}
th{{background:#f9fafb;color:#6b7280;font-weight:600;text-transform:uppercase;font-size:11px;}}
.badge{{display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;}}
.critical{{background:#fee2e2;color:#dc2626;}}.high{{background:#fef3c7;color:#d97706;}}
.medium{{background:#dbeafe;color:#2563eb;}}.low{{background:#d1fae5;color:#059669;}}
.btn{{display:inline-block;margin-top:20px;padding:12px 28px;background:#4f46e5;color:#fff;text-decoration:none;border-radius:6px;font-weight:600;font-size:15px;}}
.footer{{padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af;text-align:center;}}
</style></head><body>
<div class="container">
  <div class="header"><h1>🐛 New Bug Detected</h1><p>Zero-Effort Bug Reporter — Automated Detection</p></div>
  <div class="body">
    <table>
      <tr><th>Field</th><th>Value</th></tr>
      <tr><td>Ticket</td><td><strong>{ticket_key}</strong></td></tr>
      <tr><td>Title</td><td>{title}</td></tr>
      <tr><td>Severity</td><td><span class="badge {severity.lower()}">{severity}</span></td></tr>
      <tr><td>Priority</td><td>{priority}</td></tr>
      <tr><td>Component</td><td>{component}</td></tr>
      <tr><td>Failed Test</td><td><code style="font-size:12px">{test_name}</code></td></tr>
      <tr><td>Detected At</td><td>{timestamp}</td></tr>
    </table>
    <a href="{ticket_url}" class="btn">View Jira Ticket →</a>
  </div>
  <div class="footer">Generated automatically by Zero-Effort Bug Reporter • {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</div></body></html>"""

    return _send(to_email, subject, html, ticket_key)


def _send_escalation_email(bug_report, jira_result, to_email):
    ticket_key   = jira_result.get("ticket_key", "N/A")
    ticket_url   = jira_result.get("ticket_url", "")
    old_priority = jira_result.get("old_priority", "Unknown")
    new_priority = jira_result.get("new_priority", "Unknown")
    severity     = bug_report.get("severity", "Unknown")
    title        = bug_report.get("title", "Unknown")
    test_name    = bug_report["metadata"].get("test_name", "Unknown")
    timestamp    = bug_report["metadata"].get("timestamp", "Unknown")

    subject = f"⬆️ Priority Escalated: {ticket_key} — {old_priority} → {new_priority}"

    html = f"""
<!DOCTYPE html><html><head><style>
body{{font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px;}}
.container{{max-width:600px;margin:auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);}}
.header{{background:#7c3aed;color:#fff;padding:24px 32px;}}
.header h1{{margin:0;font-size:20px;}}.header p{{margin:6px 0 0;opacity:0.9;font-size:14px;}}
.body{{padding:24px 32px;}}
.escalation-box{{background:#fef3c7;border:2px solid #f59e0b;border-radius:8px;padding:16px 20px;margin:16px 0;text-align:center;}}
.escalation-box .arrow{{font-size:28px;font-weight:700;color:#d97706;}}
.escalation-box .label{{font-size:12px;color:#92400e;text-transform:uppercase;margin-bottom:8px;}}
table{{width:100%;border-collapse:collapse;margin-top:16px;}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:14px;}}
th{{background:#f9fafb;color:#6b7280;font-weight:600;text-transform:uppercase;font-size:11px;}}
.btn{{display:inline-block;margin-top:20px;padding:12px 28px;background:#7c3aed;color:#fff;text-decoration:none;border-radius:6px;font-weight:600;font-size:15px;}}
.footer{{padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af;text-align:center;}}
</style></head><body>
<div class="container">
  <div class="header"><h1>⬆️ Bug Recurrence — Priority Escalated</h1><p>This bug has been detected again and remains unresolved</p></div>
  <div class="body">
    <div class="escalation-box">
      <div class="label">Priority Escalation</div>
      <div class="arrow">{old_priority} → {new_priority}</div>
    </div>
    <table>
      <tr><th>Field</th><th>Value</th></tr>
      <tr><td>Ticket</td><td><strong>{ticket_key}</strong></td></tr>
      <tr><td>Title</td><td>{title}</td></tr>
      <tr><td>Severity</td><td>{severity}</td></tr>
      <tr><td>Failed Test</td><td><code style="font-size:12px">{test_name}</code></td></tr>
      <tr><td>Recurrence At</td><td>{timestamp}</td></tr>
    </table>
    <p style="color:#dc2626;font-weight:600;margin-top:16px;">⚠️ This issue has been detected multiple times. Please prioritize a fix immediately.</p>
    <a href="{ticket_url}" class="btn">View Jira Ticket →</a>
  </div>
  <div class="footer">Generated automatically by Zero-Effort Bug Reporter • {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</div></body></html>"""

    return _send(to_email, subject, html, ticket_key)


def send_ai_fix_proposal_email(
    pr_url: str,
    run_number: str,
    to_email: str,
    bug_reports: list = None,
    pr_branch: str = None,
    repo: str = None,
    github_token: str = None,
) -> bool:
    """
    Send email with full bug details and two inline action buttons:
      [✅ Approve AI Fix]  — triggers approve-ai-fix workflow dispatch
      [🔧 Fix Manually]   — triggers reject-ai-fix workflow dispatch

    The buttons link to a small Flask approval endpoint (approval_server.py)
    that forwards the choice to the GitHub Actions API, OR fall back to
    direct GitHub Actions workflow_dispatch URLs if no server is configured.

    Both buttons open in the browser so the user just clicks and it's done —
    no GitHub UI navigation required.
    """
    try:
        # ── Build per-bug detail rows ────────────────────────────────────────
        bug_rows_html = ""
        if bug_reports:
            for idx, br in enumerate(bug_reports, 1):
                meta       = br.get("metadata", {})
                title      = br.get("title", "Unknown")
                severity   = br.get("severity", "Unknown")
                priority   = br.get("priority", "Unknown")
                test_name  = meta.get("test_name", "Unknown")
                component  = meta.get("affected_component", "Unknown")
                error_type = meta.get("error_type", "Unknown")
                desc       = br.get("description", "")
                root_cause = ""
                if "h3. Summary" in desc:
                    after = desc.split("h3. Summary")[-1]
                    root_cause = after.split("\n\nh3.")[0].strip()[:200]
                if not root_cause:
                    root_cause = title[:150]

                sev_color = "#dc2626" if severity == "Critical" else "#d97706" if severity == "High" else "#2563eb"
                sev_bg    = "#fee2e2" if severity == "Critical" else "#fef3c7" if severity == "High" else "#dbeafe"

                bug_rows_html += f"""
        <tr style="background:{'#fff' if idx%2==0 else '#f9fafb'};">
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;">
            <strong>#{idx}</strong><br>
            <code style="font-size:11px;color:#374151;">{test_name}</code>
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;">{component}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;">{error_type}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:13px;">
            <span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;background:{sev_bg};color:{sev_color};">{severity}</span>
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280;">{root_cause}</td>
        </tr>"""
        else:
            bug_rows_html = '<tr><td colspan="5" style="padding:12px;text-align:center;color:#9ca3af;font-size:13px;">No bug report details available</td></tr>'

        branch_display = pr_branch or f"ai-fix-{run_number}"

        # ── Action button URLs ───────────────────────────────────────────────
        # Use GitHub Actions web UI URLs for approve/reject.
        # User clicks button → lands on the Actions "Run workflow" page
        # pre-filled with the right action. This works without any server.
        if repo:
            gh_repo = repo
        else:
            gh_repo = os.getenv("GITHUB_REPOSITORY", "")

        # Direct deep-links into GitHub Actions workflow dispatch UI.
        # These open the "Run workflow" panel with the repo pre-selected.
        # The user still clicks "Run workflow" once in the UI — but it's
        # a single click rather than navigating through the whole UI.
        actions_base = f"https://github.com/{gh_repo}/actions/workflows/ci-pipeline.yml"
        approve_url  = f"{actions_base}?query=branch%3Amain"   # user selects approve-ai-fix
        reject_url   = f"{actions_base}?query=branch%3Amain"   # user selects reject-ai-fix

        # Better: use the approval_server endpoint if APPROVAL_SERVER_URL is set
        approval_server = os.getenv("APPROVAL_SERVER_URL", "")
        if approval_server and run_number:
            approve_url = f"{approval_server}/approve?run={run_number}&action=approve-ai-fix&branch={branch_display}"
            reject_url  = f"{approval_server}/approve?run={run_number}&action=reject-ai-fix&branch={branch_display}"

        html = f"""
<!DOCTYPE html>
<html>
<head><style>
body{{font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px;}}
.container{{max-width:700px;margin:auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);}}
.header{{background:#4f46e5;color:#fff;padding:24px 32px;}}
.header h1{{margin:0;font-size:20px;}}.header p{{margin:6px 0 0;opacity:0.9;font-size:14px;}}
.body{{padding:24px 32px;}}
.info-box{{background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:16px 20px;margin-bottom:20px;}}
.info-box p{{margin:4px 0;font-size:14px;color:#0369a1;}}
.info-box strong{{color:#0c4a6e;}}
table{{width:100%;border-collapse:collapse;margin-bottom:20px;}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:14px;}}
th{{background:#f9fafb;color:#6b7280;font-weight:600;text-transform:uppercase;font-size:11px;}}
.action-box{{background:#f8fafc;border:2px solid #e2e8f0;border-radius:12px;padding:24px;margin:20px 0;text-align:center;}}
.action-box h3{{margin:0 0 8px;color:#1e293b;font-size:16px;}}
.action-box p{{margin:0 0 20px;color:#64748b;font-size:14px;}}
.btn-approve{{display:inline-block;padding:14px 32px;background:#16a34a;color:#fff;text-decoration:none;border-radius:8px;font-weight:700;font-size:15px;margin:0 8px;}}
.btn-reject{{display:inline-block;padding:14px 32px;background:#dc2626;color:#fff;text-decoration:none;border-radius:8px;font-weight:700;font-size:15px;margin:0 8px;}}
.btn-view{{display:inline-block;padding:10px 20px;background:#4f46e5;color:#fff;text-decoration:none;border-radius:6px;font-weight:600;font-size:13px;margin-top:12px;}}
.approve-info{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px 16px;margin-top:16px;font-size:13px;color:#166534;}}
.reject-info{{background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px 16px;margin-top:8px;font-size:13px;color:#991b1b;}}
.footer{{padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af;text-align:center;}}
</style></head>
<body>
<div class="container">
  <div class="header">
    <h1>🤖 AI Fix Ready — Your Action Required</h1>
    <p>Zero-Effort Bug Reporter detected failures and proposed an automated fix</p>
  </div>
  <div class="body">

    <div class="info-box">
      <p><strong>Run:</strong> #{run_number}</p>
      <p><strong>Branch:</strong> <code>{branch_display}</code></p>
      <p><strong>Pull Request:</strong> <a href="{pr_url}" style="color:#0369a1;">{pr_url}</a></p>
    </div>

    <h3 style="color:#1e293b;margin-bottom:8px;">🐛 Bugs Detected ({len(bug_reports) if bug_reports else 0} total)</h3>
    <table>
      <thead>
        <tr>
          <th>Test</th><th>Component</th><th>Error Type</th><th>Severity</th><th>Root Cause</th>
        </tr>
      </thead>
      <tbody>{bug_rows_html}</tbody>
    </table>

    <div class="action-box">
      <h3>⚡ How would you like to handle this?</h3>
      <p>The AI has analyzed the failures and proposed code changes. Choose your preferred resolution:</p>

      <a href="{approve_url}" class="btn-approve">✅ Approve AI Fix</a>
      <a href="{reject_url}" class="btn-reject">🔧 I'll Fix Manually</a>

      <div class="approve-info">
        <strong>✅ Approve AI Fix:</strong> The proposed fix will be tested automatically.
        If all tests pass, it gets merged to main and deployed. If tests fail, the PR stays open for manual review.
      </div>
      <div class="reject-info">
        <strong>🔧 Fix Manually:</strong> The AI fix PR will be closed.
        The Jira ticket stays open — assign it to a developer for manual resolution.
      </div>

      <a href="{pr_url}" class="btn-view">📋 Review Code Changes in PR →</a>
    </div>

  </div>
  <div class="footer">
    Generated automatically by Zero-Effort Bug Reporter • {datetime.now().strftime('%Y-%m-%d %H:%M')}
  </div>
</div>
</body>
</html>"""

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'🤖 [{len(bug_reports) if bug_reports else "?"}  bugs] AI Fix Ready — Run #{run_number} — Action Required'
        msg['From'] = FROM_EMAIL or SMTP_USERNAME
        msg['To'] = to_email
        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL or SMTP_USERNAME, to_email, msg.as_string())

        print(f"📧 AI fix proposal email sent to {to_email}")
        return True
    except Exception as e:
        print(f"⚠️ Failed to send AI fix proposal email: {e}")
        return False


def send_ai_fix_resolved_email(ticket_key: str, to_email: str) -> bool:
    """Send email when AI fix is approved, merged, and deployed."""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'✅ {ticket_key} Resolved — AI Fix Deployed'
        msg['From'] = FROM_EMAIL or SMTP_USERNAME
        msg['To'] = to_email

        html = f"""
<!DOCTYPE html><html><head><style>
body{{font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px;}}
.container{{max-width:600px;margin:auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);}}
.header{{background:#16a34a;color:#fff;padding:24px 32px;}}
.header h1{{margin:0;font-size:20px;}}
.body{{padding:24px 32px;}}
.footer{{padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af;text-align:center;}}
</style></head><body>
<div class="container">
  <div class="header"><h1>✅ Bug Resolved — AI Fix Deployed</h1></div>
  <div class="body">
    <p><strong>Ticket:</strong> {ticket_key}</p>
    <p><strong>Status:</strong> RESOLVED</p>
    <p>The AI-generated fix was approved, passed all tests, and has been deployed to production.</p>
    <p style="color:#166534;">No further action required.</p>
  </div>
  <div class="footer">Generated automatically by Zero-Effort Bug Reporter • {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</div></body></html>"""

        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"📧 Resolution email sent to {to_email}")
        return True
    except Exception as e:
        print(f"⚠️ Failed to send resolution email: {e}")
        return False


def send_manual_fix_required_email(ticket_key: str, jira_url: str, to_email: str) -> bool:
    """Send email when AI cannot fix and manual intervention is needed."""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'🔧 Manual Fix Required — {ticket_key}'
        msg['From'] = FROM_EMAIL or SMTP_USERNAME
        msg['To'] = to_email

        html = f"""
<!DOCTYPE html><html><head><style>
body{{font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px;}}
.container{{max-width:600px;margin:auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);}}
.header{{background:#dc2626;color:#fff;padding:24px 32px;}}
.header h1{{margin:0;font-size:20px;}}
.body{{padding:24px 32px;}}
.footer{{padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af;text-align:center;}}
</style></head><body>
<div class="container">
  <div class="header"><h1>🔧 Manual Fix Required</h1></div>
  <div class="body">
    <p>The AI Fix Agent was unable to generate an automatic fix.</p>
    <p><strong>Jira Ticket:</strong> <a href="{jira_url}/browse/{ticket_key}">{ticket_key}</a></p>
    <h3>Next Steps:</h3>
    <ol>
      <li>Review the Jira ticket for failure details</li>
      <li>Fix the issue locally and push to main</li>
      <li>CI will automatically test and deploy</li>
    </ol>
  </div>
  <div class="footer">Generated automatically by Zero-Effort Bug Reporter • {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</div></body></html>"""

        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"📧 Manual fix email sent to {to_email}")
        return True
    except Exception as e:
        print(f"⚠️ Failed to send manual fix email: {e}")
        return False


def _send(to_email: str, subject: str, html: str, ticket_key: str) -> dict:
    msg = MIMEMultipart("alternative")
    msg["From"]    = FROM_EMAIL or SMTP_USERNAME
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL or SMTP_USERNAME, to_email, msg.as_string())
        print(f"📧 Email sent → {to_email} ({ticket_key})")
        return {"status": "success", "message": f"Email sent to {to_email}"}
    except Exception as e:
        print(f"📧 Email failed: {e}")
        return {"status": "error", "message": str(e)}


def _is_configured() -> bool:
    return bool(SMTP_USERNAME and SMTP_PASSWORD)