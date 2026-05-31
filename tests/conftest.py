"""
tests/conftest.py
-----------------
Two responsibilities:

1. FIXTURE — `driver`
   Creates a headless Chrome browser before each test and destroys it
   after, even if the test crashes. Any test function that lists `driver`
   as a parameter gets this automatically from pytest.

2. HOOK — pytest_runtest_makereport
   Called by pytest after every test phase (setup / call / teardown).
   When the "call" phase fails, it captures:
     - Stack trace  → artifacts/stacktraces/
     - Console logs → artifacts/logs/
     - Screenshot   → artifacts/screenshots/
     - Metadata JSON → artifacts/failure_<name>_<timestamp>.json

   That metadata JSON is the input to the AI agent in Layer 4.
"""

import json
import os
import platform
import pytest
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

BASE_URL     = os.getenv("APP_URL", "http://localhost:5000")
ARTIFACT_DIR = os.path.join(os.path.dirname(__file__), "..", "artifacts")


def _safe(name: str) -> str:
    """Sanitise a test node ID so it can be used as a filename."""
    for ch in " []/:.":
        name = name.replace(ch, "_")
    return name


# ── Fixture ─────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def driver():
    """
    Headless Chrome fixture.
    Works on both Windows (local) and Linux (GitHub Actions).

    scope="function" → a brand-new browser per test.
    This is slower than scope="session" but guarantees zero state leakage
    between tests (no leftover cookies, sessions, or local storage).
    """
    opts = Options()
    opts.add_argument("--headless=new")          # Chrome 112+ headless
    opts.add_argument("--no-sandbox")            # Mandatory inside CI / Docker
    opts.add_argument("--disable-dev-shm-usage") # Avoids /dev/shm OOM in containers
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")    # Faster startup in CI
    opts.add_argument("--disable-software-rasterizer")
    opts.add_argument("--window-size=1280,800")  # Consistent viewport for screenshots

    # GitHub Actions — point to the installed Chrome binary explicitly
    if platform.system() == "Linux":
        chrome_bin = (
            os.getenv("CHROME_BIN")                        # set by setup-chrome action
            or "/usr/bin/google-chrome"                    # default Linux path
            or "/usr/bin/chromium-browser"                 # fallback
        )
        if os.path.exists(chrome_bin):
            opts.binary_location = chrome_bin

    # Enable browser-level log capture so we can read console.error() calls
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})

    # Use Service to suppress chromedriver version mismatch warnings
    service = Service()
    d = webdriver.Chrome(service=service, options=opts)
    d.implicitly_wait(5)  # Up to 5s before raising NoSuchElementException

    yield d

    d.quit()  # Always runs — even when the test raises an exception


# ── Failure hook ─────────────────────────────────────────────────────

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """
    Capture artifacts the moment a test fails.

    tryfirst=True  → runs before other plugins (e.g. pytest-html)
                     so artifacts exist before reports are written.
    hookwrapper=True → lets us inspect the result after pytest runs the test.
    """
    outcome = yield
    report  = outcome.get_result()

    # Only act on the test body phase when it fails
    if report.when != "call" or not report.failed:
        return

    test_name = item.nodeid
    safe_name = _safe(test_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Make sure all artifact subdirs exist
    for sub in ("stacktraces", "logs", "screenshots"):
        os.makedirs(os.path.join(ARTIFACT_DIR, sub), exist_ok=True)

    # ── 1. Stack trace ───────────────────────────────────────────────
    trace_text = (
        report.longreprtext
        if hasattr(report, "longreprtext")
        else str(report.longrepr)
    )
    trace_path = os.path.join(
        ARTIFACT_DIR, "stacktraces", f"{safe_name}_{timestamp}.log"
    )
    with open(trace_path, "w", encoding="utf-8") as f:
        f.write(f"TEST: {test_name}\n")
        f.write(f"TIME: {timestamp}\n")
        f.write(f"FILE: {item.fspath}\n\n")
        f.write("=" * 60 + "\n\n")
        f.write(trace_text)

    # ── 2. Browser console logs ──────────────────────────────────────
    log_path   = None
    driver_obj = item.funcargs.get("driver")  # None for non-Selenium tests

    if driver_obj is not None:
        try:
            entries = driver_obj.get_log("browser")
            content = f"CONSOLE LOGS — {test_name}\nTime: {timestamp}\n\n"
            content += "\n".join(
                f"[{e['level']}] {e['message']}" for e in entries
            ) or "(no log entries)"

            log_path = os.path.join(
                ARTIFACT_DIR, "logs", f"{safe_name}_{timestamp}.log"
            )
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as exc:
            print(f"\n⚠️  Console log capture failed: {exc}")

    # ── 3. Screenshot ────────────────────────────────────────────────
    screenshot_path = None

    if driver_obj is not None:
        try:
            screenshot_path = os.path.join(
                ARTIFACT_DIR, "screenshots", f"{safe_name}_{timestamp}.png"
            )
            driver_obj.save_screenshot(screenshot_path)
        except Exception as exc:
            print(f"\n⚠️  Screenshot capture failed: {exc}")
            screenshot_path = None

    # ── 4. Metadata JSON (input to AI agent) ─────────────────────────
    metadata = {
        "test_name":        test_name,
        "timestamp":        timestamp,
        "file":             str(item.fspath),
        "duration":         round(report.duration, 3),
        "failure_summary":  trace_text[:500],
        "stacktrace_file":  trace_path,
        "console_log_file": log_path,
        "screenshot_file":  screenshot_path,
    }
    meta_path = os.path.join(
        ARTIFACT_DIR, f"failure_{safe_name}_{timestamp}.json"
    )
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"\n🔴 FAILURE CAPTURED — {test_name}")
    print(f"   stack trace  → {trace_path}")
    print(f"   console logs → {log_path or 'N/A'}")
    print(f"   screenshot   → {screenshot_path or 'N/A'}")
    print(f"   metadata     → {meta_path}")