from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from .config import get_settings
from dotenv import load_dotenv

# Load environment variables from .env so os.getenv picks them up
load_dotenv()

# Optional local hard-coded overrides (not committed). Create app/core/secrets_local.py
# with a dict named SMTP_OVERRIDES to provide credentials locally without env vars.
try:  # pragma: no cover - optional import
    from .secrets_local import SMTP_OVERRIDES  # type: ignore
except Exception:  # pragma: no cover - absent in most setups
    SMTP_OVERRIDES = {}


def _get_smtp_settings():
    # Ensure settings is constructed so pydantic loads .env (even if unused directly)
    _ = get_settings()
    host = os.getenv("SMTP_HOST") or SMTP_OVERRIDES.get("host")
    port = int(os.getenv("SMTP_PORT", str(SMTP_OVERRIDES.get("port", 587))))
    username = os.getenv("SMTP_USERNAME") or SMTP_OVERRIDES.get("username")
    password = os.getenv("SMTP_PASSWORD") or SMTP_OVERRIDES.get("password")
    from_email = os.getenv("SMTP_FROM") or SMTP_OVERRIDES.get("from_email") or username or "noreply@example.com"
    use_tls_env = os.getenv("SMTP_USE_TLS")
    use_tls = (
        use_tls_env.lower() in ("1", "true", "yes") if isinstance(use_tls_env, str) else bool(SMTP_OVERRIDES.get("use_tls", True))
    )
    return host, port, username, password, from_email, use_tls


def send_email(to_email: str, subject: str, html_body: str, from_name: Optional[str] = None, reply_to: Optional[str] = None) -> None:
    host, port, username, password, from_email, use_tls = _get_smtp_settings()
    if not host or not username or not password:
        # In dev, if SMTP isn't configured, just noop
        print(f"[email] SMTP not configured; would send to {to_email}: {subject}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to

    part = MIMEText(html_body, "html")
    msg.attach(part)

    with smtplib.SMTP(host, port) as server:
        if use_tls:
            server.starttls()
        server.login(username, password)
        server.sendmail(from_email, [to_email], msg.as_string())
