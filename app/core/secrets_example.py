# Copy this file to secrets_local.py (same folder) and fill in your local SMTP credentials.
# This file is not imported automatically unless you create secrets_local.py.
# Do NOT commit secrets_local.py to version control.

SMTP_OVERRIDES = {
    # Gmail defaults
    "host": "smtp.gmail.com",
    "port": 587,
    "username": "barnard.juanpierre@gmail.com",
    "password": "kyxq tzei hzty fvub",  # Use a Gmail App Password (16 chars), not your normal password
    "from_email": "barnard.juanpierre@gmail.com",
    "use_tls": True,
}

# ====================================
# Email-to-Ticket Configuration
# ====================================
# Configure these to enable automatic ticket creation from incoming emails
# Clients can send emails to this address to create support tickets

# IMAP server for receiving emails (Gmail example)
SUPPORT_EMAIL_IMAP_SERVER = "imap.gmail.com"  # For Gmail
# SUPPORT_EMAIL_IMAP_SERVER = "outlook.office365.com"  # For Office 365
# SUPPORT_EMAIL_IMAP_SERVER = "imap.mail.yahoo.com"  # For Yahoo

# The email address where clients send support requests
SUPPORT_EMAIL_ADDRESS = "support@yourdomain.com"

# Email password or App Password (for Gmail with 2FA, use App Password)
# Gmail: https://myaccount.google.com/apppasswords
# Office 365: Use your regular password or app password
SUPPORT_EMAIL_PASSWORD = "your-app-password-here"

# Default user ID to assign new tickets to (None = unassigned)
# Set this to your support team lead's user ID
SUPPORT_EMAIL_DEFAULT_ASSIGNED_TO = None  # e.g., 1 for user ID 1

# How often to check for new emails (in seconds)
# 300 = 5 minutes, 600 = 10 minutes
EMAIL_CHECK_INTERVAL = 300
