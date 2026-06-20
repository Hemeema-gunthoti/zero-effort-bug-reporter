"""
notifications/email_notifier.py
--------------------------------
Sends email notifications when:
  - A new bug ticket is created
  - A duplicate is found and priority is escalated

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
    """
    Main entry point called from main.py.
    Routes to the correct email template based on ticket status.
    """
    if not _is_configured():
        print(f"📧 Email skipped — SMTP not configured in .env")
        return {"status": "skipped", "message": "SMTP not configured"}

    status     = jira_result.get("status", "unknown")
    ticket_key = jira_result.get("ticket_key", "N/A")
    ticket_url = jira_result.get("ticket_url", "")
    #assignee   = bug_report.get("assignee", "")
    to_email   = SMTP_USERNAME

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


def _send_new_ticket_email(
    bug_report:  dict,
    jira_result: dict,
    to_email:    str,
) -> dict:
    ticket_key  = jira_result.get("ticket_key", "N/A")
    ticket_url  = jira_result.get("ticket_url", "")
    severity    = bug_report.get("severity", "Unknown")
    priority    = bug_report.get("priority", "Unknown")
    title       = bug_report.get("title", "Unknown")
    test_name   = bug_report["metadata"].get("test_name", "Unknown")
    component   = bug_report["metadata"].get("affected_component", "Unknown")
    timestamp   = bug_report["metadata"].get("timestamp", "Unknown")

    subject = f"🐛 [{severity}] New Bug: {ticket_key} — {title[:60]}"

    html = f"""
<!DOCTYPE html>
<html>
<head>
  <style>
    body       {{ font-family: Arial, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }}
    .container {{ max-width: 600px; margin: auto; background: #fff;
                  border-radius: 8px; overflow: hidden;
                  box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
    .header    {{ background: {'#dc2626' if severity == 'Critical' else
                               '#d97706' if severity == 'High' else
                               '#2563eb'};
                  color: #fff; padding: 24px 32px; }}
    .header h1 {{ margin: 0; font-size: 20px; }}
    .header p  {{ margin: 6px 0 0; opacity: 0.9; font-size: 14px; }}
    .body      {{ padding: 24px 32px; }}
    .field     {{ margin-bottom: 14px; }}
    .label     {{ font-size: 12px; color: #666; text-transform: uppercase;
                  letter-spacing: 0.5px; margin-bottom: 4px; }}
    .value     {{ font-size: 15px; color: #111; font-weight: 500; }}
    .badge     {{ display: inline-block; padding: 3px 10px; border-radius: 12px;
                  font-size: 12px; font-weight: 600; }}
    .critical  {{ background: #fee2e2; color: #dc2626; }}
    .high      {{ background: #fef3c7; color: #d97706; }}
    .medium    {{ background: #dbeafe; color: #2563eb; }}
    .low       {{ background: #d1fae5; color: #059669; }}
    .btn       {{ display: inline-block; margin-top: 20px; padding: 12px 28px;
                  background: #4f46e5; color: #fff; text-decoration: none;
                  border-radius: 6px; font-weight: 600; font-size: 15px; }}
    .footer    {{ padding: 16px 32px; background: #f9fafb; border-top: 1px solid #e5e7eb;
                  font-size: 12px; color: #9ca3af; text-align: center; }}
    table      {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    th, td     {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e5e7eb;
                  font-size: 14px; }}
    th         {{ background: #f9fafb; color: #6b7280; font-weight: 600;
                  text-transform: uppercase; font-size: 11px; }}
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🐛 New Bug Detected</h1>
    <p>Zero-Effort Bug Reporter — Automated Detection</p>
  </div>
  <div class="body">
    <div class="field">
      <div class="label">Ticket</div>
      <div class="value">{ticket_key}</div>
    </div>
    <div class="field">
      <div class="label">Title</div>
      <div class="value">{title}</div>
    </div>
    <table>
      <tr>
        <th>Field</th>
        <th>Value</th>
      </tr>
      <tr>
        <td>Severity</td>
        <td><span class="badge {severity.lower()}">{severity}</span></td>
      </tr>
      <tr>
        <td>Priority</td>
        <td>{priority}</td>
      </tr>
      <tr>
        <td>Component</td>
        <td>{component}</td>
      </tr>
      <tr>
        <td>Failed Test</td>
        <td><code style="font-size:12px">{test_name}</code></td>
      </tr>
      <tr>
        <td>Detected At</td>
        <td>{timestamp}</td>
      </tr>
    </table>
    <a href="{ticket_url}" class="btn">View Jira Ticket →</a>
  </div>
  <div class="footer">
    Generated automatically by Zero-Effort Bug Reporter •
    {datetime.now().strftime('%Y-%m-%d %H:%M')}
  </div>
</div>
</body>
</html>
"""

    return _send(to_email, subject, html, ticket_key)


def _send_escalation_email(
    bug_report:  dict,
    jira_result: dict,
    to_email:    str,
) -> dict:
    ticket_key    = jira_result.get("ticket_key", "N/A")
    ticket_url    = jira_result.get("ticket_url", "")
    old_priority  = jira_result.get("old_priority", "Unknown")
    new_priority  = jira_result.get("new_priority", "Unknown")
    severity      = bug_report.get("severity", "Unknown")
    title         = bug_report.get("title", "Unknown")
    test_name     = bug_report["metadata"].get("test_name", "Unknown")
    timestamp     = bug_report["metadata"].get("timestamp", "Unknown")

    subject = f"⬆️ Priority Escalated: {ticket_key} — {old_priority} → {new_priority}"

    html = f"""
<!DOCTYPE html>
<html>
<head>
  <style>
    body       {{ font-family: Arial, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }}
    .container {{ max-width: 600px; margin: auto; background: #fff;
                  border-radius: 8px; overflow: hidden;
                  box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
    .header    {{ background: #7c3aed; color: #fff; padding: 24px 32px; }}
    .header h1 {{ margin: 0; font-size: 20px; }}
    .header p  {{ margin: 6px 0 0; opacity: 0.9; font-size: 14px; }}
    .body      {{ padding: 24px 32px; }}
    .escalation-box {{
      background: #fef3c7; border: 2px solid #f59e0b;
      border-radius: 8px; padding: 16px 20px; margin: 16px 0;
      text-align: center;
    }}
    .escalation-box .arrow {{
      font-size: 28px; font-weight: 700; color: #d97706;
    }}
    .escalation-box .label {{
      font-size: 12px; color: #92400e; text-transform: uppercase; margin-bottom: 8px;
    }}
    table      {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    th, td     {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e5e7eb;
                  font-size: 14px; }}
    th         {{ background: #f9fafb; color: #6b7280; font-weight: 600;
                  text-transform: uppercase; font-size: 11px; }}
    .btn       {{ display: inline-block; margin-top: 20px; padding: 12px 28px;
                  background: #7c3aed; color: #fff; text-decoration: none;
                  border-radius: 6px; font-weight: 600; font-size: 15px; }}
    .footer    {{ padding: 16px 32px; background: #f9fafb; border-top: 1px solid #e5e7eb;
                  font-size: 12px; color: #9ca3af; text-align: center; }}
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>⬆️ Bug Recurrence — Priority Escalated</h1>
    <p>This bug has been detected again and remains unresolved</p>
  </div>
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
    <p style="color:#dc2626; font-weight:600; margin-top:16px;">
      ⚠️ This issue has been detected multiple times.
      Please prioritize a fix immediately.
    </p>
    <a href="{ticket_url}" class="btn">View Jira Ticket →</a>
  </div>
  <div class="footer">
    Generated automatically by Zero-Effort Bug Reporter •
    {datetime.now().strftime('%Y-%m-%d %H:%M')}
  </div>
</div>
</body>
</html>
"""

    return _send(to_email, subject, html, ticket_key)


def _send(to_email: str, subject: str, html: str, ticket_key: str) -> dict:
    msg             = MIMEMultipart("alternative")
    msg["From"]     = FROM_EMAIL or SMTP_USERNAME
    msg["To"]       = to_email
    msg["Subject"]  = subject
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(
                FROM_EMAIL or SMTP_USERNAME,
                to_email,
                msg.as_string(),
            )
        print(f"📧 Email sent → {to_email} ({ticket_key})")
        return {"status": "success", "message": f"Email sent to {to_email}"}

    except Exception as e:
        print(f"📧 Email failed: {e}")
        return {"status": "error", "message": str(e)}


def _is_configured() -> bool:
    return bool(SMTP_USERNAME and SMTP_PASSWORD)


def send_ai_fix_proposal_email(pr_url: str, run_number: str, to_email: str) -> bool:
    """Send email when AI fix is proposed and needs human approval."""
    try:
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        import smtplib
        import os

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'🤖 AI Fix Proposal - Run #{run_number} - Approval Required'
        msg['From'] = os.getenv('FROM_EMAIL', os.getenv('SMTP_USERNAME'))
        msg['To'] = to_email

        html = f'''
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #4f46e5;">🤖 AI Fix Proposal</h2>
                <p>The AI Fix Agent has analyzed the failing tests and proposed code changes.</p>
                
                <div style="background: #fef3c7; padding: 15px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #f59e0b;">
                    <h3 style="margin-top: 0; color: #92400e;">⏳ Human Approval Required</h3>
                    <p><strong>Pull Request:</strong> <a href="{pr_url}" style="color: #4f46e5;">{pr_url}</a></p>
                    <p><strong>Run Number:</strong> #{run_number}</p>
                </div>

                <h3 style="color: #374151;">How to Approve:</h3>
                <ol style="padding-left: 20px;">
                    <li>Review the code changes in the PR</li>
                    <li>Go to <strong>Actions</strong> tab in your repo</li>
                    <li>Select <strong>CI/CD — Test-First Gated Deploy</strong></li>
                    <li>Click <strong>Run workflow</strong></li>
                    <li>Select <strong>approve-ai-fix</strong></li>
                </ol>

                <h3 style="color: #374151;">How to Reject:</h3>
                <ol style="padding-left: 20px;">
                    <li>Same steps, but select <strong>reject-ai-fix</strong></li>
                    <li>Jira ticket will remain open for manual fix</li>
                </ol>
            </div>
        </body>
        </html>
        '''

        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP(os.getenv('SMTP_SERVER', 'smtp.gmail.com'), int(os.getenv('SMTP_PORT', '587'))) as server:
            server.starttls()
            server.login(os.getenv('SMTP_USERNAME'), os.getenv('SMTP_PASSWORD'))
            server.send_message(msg)

        print(f"📧 AI fix proposal email sent to {to_email}")
        return True
    except Exception as e:
        print(f"⚠️ Failed to send AI fix proposal email: {e}")
        return False


def send_ai_fix_resolved_email(ticket_key: str, to_email: str) -> bool:
    """Send email when AI fix is approved, merged, and deployed."""
    try:
        import smtplib
        from email.mime.text import MIMEText
        import os

        msg = MIMEText(f'''
✅ Bug Resolved — AI Fix Deployed

Ticket: {ticket_key}
Status: RESOLVED

The AI-generated fix was approved by a human reviewer, passed all tests,
and has been deployed to production.

No further action required.
        ''')
        msg['Subject'] = f'✅ {ticket_key} Resolved — AI Fix Deployed'
        msg['From'] = os.getenv('FROM_EMAIL', os.getenv('SMTP_USERNAME'))
        msg['To'] = to_email

        with smtplib.SMTP(os.getenv('SMTP_SERVER', 'smtp.gmail.com'), int(os.getenv('SMTP_PORT', '587'))) as server:
            server.starttls()
            server.login(os.getenv('SMTP_USERNAME'), os.getenv('SMTP_PASSWORD'))
            server.send_message(msg)

        print(f"📧 Resolution email sent to {to_email}")
        return True
    except Exception as e:
        print(f"⚠️ Failed to send resolution email: {e}")
        return False


def send_manual_fix_required_email(ticket_key: str, jira_url: str, to_email: str) -> bool:
    """Send email when AI cannot fix and manual intervention is needed."""
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        import os

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'🔧 Manual Fix Required — {ticket_key}'
        msg['From'] = os.getenv('FROM_EMAIL', os.getenv('SMTP_USERNAME'))
        msg['To'] = to_email

        html = f'''
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #dc2626;">🔧 Manual Fix Required</h2>
                <p>The AI Fix Agent was unable to generate an automatic fix for the failing tests.</p>
                
                <div style="background: #fee2e2; padding: 15px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #dc2626;">
                    <h3 style="margin-top: 0; color: #991b1b;">Jira Ticket</h3>
                    <p><strong>Ticket:</strong> <a href="{jira_url}/browse/{ticket_key}" style="color: #dc2626;">{ticket_key}</a></p>
                </div>

                <h3 style="color: #374151;">Next Steps:</h3>
                <ol style="padding-left: 20px;">
                    <li>Review the Jira ticket for failure details</li>
                    <li>Fix the issue in your local environment</li>
                    <li>Push the fix to the <code>main</code> branch</li>
                    <li>CI will automatically test and deploy</li>
                </ol>

                <p style="background: #f0fdf4; padding: 10px; border-radius: 6px; color: #166534;">
                    💡 <strong>Tip:</strong> Include <code>fix:</code> in your commit message for faster processing.
                </p>
            </div>
        </body>
        </html>
        '''

        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP(os.getenv('SMTP_SERVER', 'smtp.gmail.com'), int(os.getenv('SMTP_PORT', '587'))) as server:
            server.starttls()
            server.login(os.getenv('SMTP_USERNAME'), os.getenv('SMTP_PASSWORD'))
            server.send_message(msg)

        print(f"📧 Manual fix email sent to {to_email}")
        return True
    except Exception as e:
        print(f"⚠️ Failed to send manual fix email: {e}")
        return False