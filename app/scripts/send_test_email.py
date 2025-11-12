from app.core.email import send_email
import os

recipient = os.getenv("TEST_RECIPIENT", "barnard.juanpierre@gail.com")
subject = "SMTP test"
html = "<p>Hello from ClickUp Lite test email.</p>"

try:
    send_email(recipient, subject, html, from_name="ClickUp Lite")
    print(f"Sent test email (or attempted) to {recipient}")
except Exception as e:
    print("Error sending email:", type(e).__name__, str(e))
