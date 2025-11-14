"""
Test email analysis for task title generation
Run this to verify the AI email analyzer works correctly
"""

import sys
sys.path.append('.')

from app.core.email_to_ticket_v2 import EmailToTicketService

# Mock email settings for testing
class MockEmailSettings:
    def __init__(self):
        self.incoming_mail_type = "POP3"
        self.incoming_server = "mail.example.com"
        self.incoming_port = 995
        self.incoming_username = "test@example.com"
        self.incoming_password = "password"
        self.incoming_use_ssl = True

# Test cases
test_emails = [
    {
        "subject": "Printer not working in office 3",
        "body": "Hi support, our printer stopped working this morning. Can someone come fix it?"
    },
    {
        "subject": "URGENT: Network down",
        "body": "The entire network is down. All employees cannot access the internet."
    },
    {
        "subject": "Email setup on new laptop",
        "body": "I just got a new laptop and need help setting up my email account."
    },
    {
        "subject": "Password reset request",
        "body": "I forgot my password and cannot login to my account."
    },
    {
        "subject": "Software installation needed",
        "body": "Can you please install Adobe Photoshop on my computer? Thanks."
    },
    {
        "subject": "VPN connection issues",
        "body": "I'm working from home and the VPN keeps disconnecting every few minutes."
    }
]

def test_email_analysis():
    """Test the email analysis function"""
    settings = MockEmailSettings()
    service = EmailToTicketService(settings, workspace_id=1)
    
    print("=" * 70)
    print("EMAIL TASK TITLE ANALYSIS TEST")
    print("=" * 70)
    print()
    
    for i, email in enumerate(test_emails, 1):
        print(f"Test #{i}")
        print(f"Subject: {email['subject']}")
        print(f"Body: {email['body'][:50]}...")
        
        # Analyze email
        title, description = service.analyze_email_for_task(email['subject'], email['body'])
        priority = service.determine_priority(email['subject'], email['body'])
        
        print(f"→ Generated Title: '{title}' (length: {len(title)} chars)")
        print(f"→ Priority: {priority}")
        print()
    
    print("=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)
    print()
    print("✅ All titles should be concise (ideally 3 words or less)")
    print("✅ Priorities should match urgency keywords")
    print()

if __name__ == "__main__":
    test_email_analysis()
