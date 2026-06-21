"""
email_notifier.py
Sends a rich HTML email with full bug details and two CTA buttons:
  - [Fix with AI]  → triggers the ai-fix workflow via webhook
  - [Fix Manually] → links to the GitHub Actions run
"""

import os
import json
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# ──────────────────────────────────────────────
# Config from environment
# ──────────────────────────────────────────────
SMTP_HOST        = os.environ.get("SMTP_HOST")  or "smtp.gmail.com"
SMTP_PORT        = int(os.environ.get("SMTP_PORT") or 587)
SMTP_USER        = os.environ.get("SMTP_USER")  or ""
SMTP_PASSWORD    = os.environ.get("SMTP_PASSWORD") or ""
NOTIFY_EMAIL     = os.environ.get("NOTIFY_EMAIL") or ""
REPO             = os.environ.get("REPO")        or ""
BRANCH           = os.environ.get("BRANCH")      or "main"
COMMIT_SHA       = os.environ.get("COMMIT_SHA")  or ""
RUN_ID           = os.environ.get("RUN_ID")      or ""
TESTS_PASSED     = (os.environ.get("TESTS_PASSED") or "false").lower() == "true"
FAILURE_SUMMARY  = os.environ.get("FAILURE_SUMMARY") or ""
WEBHOOK_BASE_URL = (os.environ.get("WEBHOOK_BASE_URL") or "").rstrip("/")
EMAIL_MODE       = os.environ.get("EMAIL_MODE")  or "bug_report"  # bug_report | ai_fix_success | ai_fix_failed


def load_failed_tests() -> list[dict]:
    """Try to load failed test details from test_results.json"""
    try:
        with open("test_results.json") as f:
            data = json.load(f)
        failed = []
        for t in data.get("tests", []):
            if t.get("outcome") == "failed":
                failed.append({
                    "name":  t["nodeid"],
                    "error": t.get("call", {}).get("longrepr", "No details")[:600],
                })
        return failed
    except Exception:
        return []


def _ai_fix_url() -> str:
    """Webhook URL that triggers the AI fix workflow."""
    return (
        f"{WEBHOOK_BASE_URL}/trigger-ai-fix"
        f"?repo={REPO}"
        f"&branch={BRANCH}"
        f"&commit_sha={COMMIT_SHA}"
        f"&run_id={RUN_ID}"
    )


def _manual_url() -> str:
    """Direct link to the GitHub Actions run."""
    return f"https://github.com/{REPO}/actions/runs/{RUN_ID}"


def _failed_tests_html(failed_tests: list[dict]) -> str:
    if not failed_tests:
        return "<p style='color:#6b7280;'>No detailed failure info available.</p>"

    rows = ""
    for i, t in enumerate(failed_tests, 1):
        error_escaped = (
            t["error"]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        rows += f"""
        <div style="margin-bottom:16px; padding:12px 16px;
                    background:#1e1e2e; border-radius:8px;
                    border-left:4px solid #ef4444;">
          <p style="margin:0 0 6px 0; font-size:13px; font-weight:600;
                    color:#f87171; font-family:monospace;">
            #{i}  {t['name']}
          </p>
          <pre style="margin:0; font-size:11px; color:#d1d5db;
                      white-space:pre-wrap; word-break:break-all;
                      font-family:monospace;">{error_escaped}</pre>
        </div>
        """
    return rows


def _build_bug_report_email(failed_tests: list[dict]) -> str:
    short_sha = COMMIT_SHA[:8] if COMMIT_SHA else "unknown"
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    test_count = len(failed_tests)
    status_badge = (
        '<span style="background:#ef4444;color:#fff;padding:3px 10px;'
        'border-radius:12px;font-size:12px;font-weight:700;">FAILED</span>'
        if not TESTS_PASSED else
        '<span style="background:#22c55e;color:#fff;padding:3px 10px;'
        'border-radius:12px;font-size:12px;font-weight:700;">PASSED</span>'
    )

    failed_section = ""
    if not TESTS_PASSED and failed_tests:
        failed_section = f"""
        <div style="margin:24px 0;">
          <h3 style="margin:0 0 12px 0; color:#f87171;
                     font-size:15px; font-weight:700;">
            ❌ Failed Tests ({test_count})
          </h3>
          {_failed_tests_html(failed_tests)}
        </div>
        """

    cta_section = ""
    if not TESTS_PASSED:
        cta_section = f"""
        <div style="margin:32px 0; text-align:center;">
          <p style="color:#9ca3af; font-size:14px; margin-bottom:20px;">
            How would you like to resolve these failures?
          </p>

          <!-- AI Fix Button -->
          <a href="{_ai_fix_url()}"
             style="display:inline-block; margin:8px 12px;
                    padding:14px 32px; background:#6366f1;
                    color:#ffffff; text-decoration:none;
                    border-radius:8px; font-size:15px;
                    font-weight:700; letter-spacing:0.3px;
                    box-shadow:0 4px 14px rgba(99,102,241,0.4);">
            🤖 Fix with AI
          </a>

          <!-- Manual Fix Button -->
          <a href="{_manual_url()}"
             style="display:inline-block; margin:8px 12px;
                    padding:14px 32px; background:#374151;
                    color:#ffffff; text-decoration:none;
                    border-radius:8px; font-size:15px;
                    font-weight:700; letter-spacing:0.3px;">
            🔧 Fix Manually
          </a>

          <p style="margin-top:20px; font-size:12px; color:#6b7280;">
            <strong style="color:#6366f1;">Fix with AI</strong>
            will trigger the AI agent to analyze the failures, apply patches,
            re-run all tests, and auto-merge only if every test passes.<br><br>
            <strong>Fix Manually</strong>
            opens the GitHub Actions run so you can investigate and push your own fix.
          </p>
        </div>
        """
    else:
        cta_section = f"""
        <div style="margin:24px 0; text-align:center;">
          <a href="{_manual_url()}"
             style="display:inline-block; padding:12px 28px;
                    background:#22c55e; color:#fff;
                    text-decoration:none; border-radius:8px;
                    font-size:14px; font-weight:700;">
            ✅ View Successful Run
          </a>
        </div>
        """

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Zero-Effort Bug Reporter</title>
</head>
<body style="margin:0; padding:0; background:#0f1117; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">

  <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
         style="background:#0f1117; min-height:100vh; padding:40px 16px;">
    <tr>
      <td align="center">

        <!-- Card -->
        <table width="620" cellpadding="0" cellspacing="0" role="presentation"
               style="background:#1a1d27; border-radius:16px;
                      border:1px solid #2d3148; overflow:hidden;
                      box-shadow:0 8px 32px rgba(0,0,0,0.4);">

          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#1e1b4b 0%,#312e81 100%);
                       padding:28px 32px; border-bottom:1px solid #2d3148;">
              <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
                <tr>
                  <td>
                    <p style="margin:0; font-size:12px; color:#818cf8;
                               text-transform:uppercase; letter-spacing:1px;
                               font-weight:600;">Zero-Effort Bug Reporter</p>
                    <h1 style="margin:6px 0 0 0; font-size:22px;
                                color:#e0e7ff; font-weight:800;">
                      {'⚠️ Build Failure Detected' if not TESTS_PASSED else '✅ Build Passed'}
                    </h1>
                  </td>
                  <td align="right" style="vertical-align:top; padding-top:4px;">
                    {status_badge}
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Meta info -->
          <tr>
            <td style="padding:24px 32px 0 32px;">
              <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
                     style="background:#111827; border-radius:10px;
                            border:1px solid #1f2937; overflow:hidden;">
                <tr>
                  <td style="padding:16px 20px;">
                    <table width="100%" cellpadding="0" cellspacing="0">
                      {"".join(f'''
                      <tr>
                        <td style="padding:5px 0; color:#6b7280;
                                   font-size:13px; width:130px;">{k}</td>
                        <td style="padding:5px 0; color:#e5e7eb;
                                   font-size:13px; font-family:monospace;">{v}</td>
                      </tr>
                      ''' for k,v in [
                          ("Repository",   REPO or "—"),
                          ("Branch",       BRANCH or "—"),
                          ("Commit",       f"{short_sha}"),
                          ("Run ID",       f"#{RUN_ID}"),
                          ("Timestamp",    timestamp),
                          ("Tests Failed", str(test_count) if not TESTS_PASSED else "0"),
                      ])}
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:24px 32px;">
              {failed_section}
              {cta_section}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:16px 32px 28px 32px; border-top:1px solid #1f2937;">
              <p style="margin:0; font-size:11px; color:#4b5563; text-align:center;">
                This is an automated notification from Zero-Effort Bug Reporter.<br>
                <a href="https://github.com/{REPO}" style="color:#6366f1; text-decoration:none;">
                  View Repository
                </a>
                &nbsp;·&nbsp;
                <a href="{_manual_url()}" style="color:#6366f1; text-decoration:none;">
                  View Run
                </a>
              </p>
            </td>
          </tr>

        </table>
        <!-- /Card -->

      </td>
    </tr>
  </table>

</body>
</html>
"""


def _build_ai_fix_success_email() -> str:
    short_sha = COMMIT_SHA[:8] if COMMIT_SHA else "unknown"
    return f"""
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0f1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0f1117;padding:40px 16px;">
  <tr><td align="center">
    <table width="620" cellpadding="0" cellspacing="0"
           style="background:#1a1d27;border-radius:16px;border:1px solid #2d3148;">
      <tr>
        <td style="background:linear-gradient(135deg,#052e16 0%,#14532d 100%);
                   padding:28px 32px;border-bottom:1px solid #2d3148;">
          <h1 style="margin:0;color:#d1fae5;font-size:22px;font-weight:800;">
            🎉 AI Fix Merged Successfully
          </h1>
        </td>
      </tr>
      <tr>
        <td style="padding:28px 32px;">
          <p style="color:#d1d5db;font-size:15px;line-height:1.6;">
            The AI agent analyzed the failing tests, applied fixes, re-ran the full
            test suite, and all tests passed. The fix has been automatically merged
            into <strong style="color:#86efac;">main</strong>.
          </p>
          <table cellpadding="0" cellspacing="0" style="margin-top:12px;
                 background:#111827;border-radius:8px;border:1px solid #1f2937;">
            <tr>
              <td style="padding:14px 20px;color:#6b7280;font-size:13px;width:130px;">Repository</td>
              <td style="padding:14px 20px;color:#e5e7eb;font-size:13px;font-family:monospace;">{REPO}</td>
            </tr>
            <tr>
              <td style="padding:14px 20px;color:#6b7280;font-size:13px;">Branch</td>
              <td style="padding:14px 20px;color:#e5e7eb;font-size:13px;font-family:monospace;">{BRANCH} → main</td>
            </tr>
            <tr>
              <td style="padding:14px 20px;color:#6b7280;font-size:13px;">Commit</td>
              <td style="padding:14px 20px;color:#e5e7eb;font-size:13px;font-family:monospace;">{short_sha}</td>
            </tr>
          </table>
          <div style="margin-top:28px;text-align:center;">
            <a href="https://github.com/{REPO}/commits/main"
               style="display:inline-block;padding:12px 28px;background:#22c55e;
                      color:#fff;text-decoration:none;border-radius:8px;
                      font-size:14px;font-weight:700;">
              View Commit History
            </a>
          </div>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body></html>
"""


def _build_ai_fix_failed_email() -> str:
    return f"""
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0f1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0f1117;padding:40px 16px;">
  <tr><td align="center">
    <table width="620" cellpadding="0" cellspacing="0"
           style="background:#1a1d27;border-radius:16px;border:1px solid #2d3148;">
      <tr>
        <td style="background:linear-gradient(135deg,#450a0a 0%,#7f1d1d 100%);
                   padding:28px 32px;border-bottom:1px solid #2d3148;">
          <h1 style="margin:0;color:#fecaca;font-size:22px;font-weight:800;">
            🤖 AI Fix Attempted — Tests Still Failing
          </h1>
        </td>
      </tr>
      <tr>
        <td style="padding:28px 32px;">
          <p style="color:#d1d5db;font-size:15px;line-height:1.6;">
            The AI agent applied fixes but the test suite still has failures.
            <strong style="color:#f87171;">No merge was performed.</strong>
            Manual intervention is required.
          </p>
          <div style="margin-top:28px;text-align:center;">
            <a href="{_manual_url()}"
               style="display:inline-block;padding:12px 28px;background:#ef4444;
                      color:#fff;text-decoration:none;border-radius:8px;
                      font-size:14px;font-weight:700;">
              🔧 Fix Manually
            </a>
          </div>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body></html>
"""


def send_email(subject: str, html_body: str) -> None:
    if not all([SMTP_USER, SMTP_PASSWORD, NOTIFY_EMAIL]):
        print("[email_notifier] Missing SMTP credentials or NOTIFY_EMAIL — skipping.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = NOTIFY_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, NOTIFY_EMAIL, msg.as_string())

    print(f"[email_notifier] Email sent → {NOTIFY_EMAIL}")


def main():
    failed_tests = load_failed_tests()

    if EMAIL_MODE == "ai_fix_success":
        subject = f"✅ AI Fix Merged — {REPO} [{BRANCH}]"
        body = _build_ai_fix_success_email()

    elif EMAIL_MODE == "ai_fix_failed":
        subject = f"❌ AI Fix Failed — Manual Review Needed — {REPO}"
        body = _build_ai_fix_failed_email()

    else:  # default: bug_report
        status = "PASSED ✅" if TESTS_PASSED else f"FAILED ❌ ({len(failed_tests)} test(s))"
        subject = f"[Bug Report] Build {status} — {REPO} [{BRANCH[:20]}]"
        body = _build_bug_report_email(failed_tests)

    send_email(subject, body)


if __name__ == "__main__":
    main()