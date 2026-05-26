"""
config/settings.py
------------------
Single source of truth for all configuration.
Every value is read from environment variables (your .env file).
Nothing is hardcoded here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

class Settings:

    # ── Groq ──────────────────────────────────────────────────────────
    GROQ_API_KEY  = os.getenv("GROQ_API_KEY")
    GROQ_MODEL    = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # ── Jira ──────────────────────────────────────────────────────────
    JIRA_BASE_URL       = os.getenv("JIRA_BASE_URL")
    JIRA_EMAIL          = os.getenv("JIRA_EMAIL")
    JIRA_API_TOKEN      = os.getenv("JIRA_API_TOKEN")
    JIRA_PROJECT_KEY    = os.getenv("JIRA_PROJECT_KEY", "BUG")
    JIRA_SEVERITY_FIELD = os.getenv("JIRA_SEVERITY_FIELD", "customfield_10105")

    # ── Email ──────────────────────────────────────────────────────────
    SMTP_SERVER   = os.getenv("SMTP_SERVER",   "smtp.gmail.com")
    SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    FROM_EMAIL    = os.getenv("FROM_EMAIL")

    # ── Redis ──────────────────────────────────────────────────────────
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # ── App ────────────────────────────────────────────────────────────
    APP_URL = os.getenv("APP_URL", "http://localhost:5000")

    # ── Agent behaviour ────────────────────────────────────────────────
    MAX_STACKTRACE_LENGTH = int(os.getenv("MAX_STACKTRACE_LENGTH", "5000"))
    MAX_LOG_LENGTH        = int(os.getenv("MAX_LOG_LENGTH",        "2000"))

    @classmethod
    def validate(cls):
        """
        Call this at startup to catch missing critical config early
        rather than failing mid-pipeline with a cryptic error.
        """
        missing = []

        if not cls.GROQ_API_KEY:
            missing.append("GROQ_API_KEY")
        if not cls.JIRA_BASE_URL:
            missing.append("JIRA_BASE_URL")
        if not cls.JIRA_EMAIL:
            missing.append("JIRA_EMAIL")
        if not cls.JIRA_API_TOKEN:
            missing.append("JIRA_API_TOKEN")

        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                f"Check your .env file."
            )


settings = Settings()