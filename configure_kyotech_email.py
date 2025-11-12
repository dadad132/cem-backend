"""
Configure email settings with KyoTech mail server
"""
import sqlite3
from datetime import datetime

def configure_email():
    """Update email settings with KyoTech credentials"""
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    print("=" * 60)
    print("CONFIGURE EMAIL SETTINGS - KyoTech Mail Server")
    print("=" * 60)
    
    try:
        # Check if settings exist
        cursor.execute("SELECT id FROM emailsettings WHERE workspace_id = 1")
        existing = cursor.fetchone()
        
        if existing:
            print("\n[1] Updating existing email settings...")
            cursor.execute("""
                UPDATE emailsettings 
                SET smtp_host = ?,
                    smtp_port = ?,
                    smtp_username = ?,
                    smtp_password = ?,
                    smtp_from_email = ?,
                    smtp_from_name = ?,
                    smtp_use_tls = ?,
                    incoming_mail_type = ?,
                    incoming_mail_host = ?,
                    incoming_mail_port = ?,
                    incoming_mail_username = ?,
                    incoming_mail_password = ?,
                    incoming_mail_use_ssl = ?,
                    webmail_url = ?,
                    company_name = ?,
                    auto_reply_enabled = ?,
                    updated_at = ?
                WHERE workspace_id = 1
            """, (
                'mail.kyotech.co.za',  # smtp_host
                587,  # smtp_port
                'support@kyotech.co.za',  # smtp_username
                'r64Er4hTWf',  # smtp_password
                'support@kyotech.co.za',  # smtp_from_email
                'KyoTech Support',  # smtp_from_name
                1,  # smtp_use_tls (True)
                'POP3',  # incoming_mail_type
                'mail.kyotech.co.za',  # incoming_mail_host
                110,  # incoming_mail_port
                'support@kyotech.co.za',  # incoming_mail_username
                'r64Er4hTWf',  # incoming_mail_password
                0,  # incoming_mail_use_ssl (False for port 110)
                'www.kyotech.co.za/webmail',  # webmail_url
                'KyoTech Support',  # company_name
                1,  # auto_reply_enabled (True)
                datetime.utcnow().isoformat()  # updated_at
            ))
        else:
            print("\n[1] Creating new email settings...")
            cursor.execute("""
                INSERT INTO emailsettings (
                    workspace_id, smtp_host, smtp_port, smtp_username, smtp_password,
                    smtp_from_email, smtp_from_name, smtp_use_tls,
                    incoming_mail_type, incoming_mail_host, incoming_mail_port,
                    incoming_mail_username, incoming_mail_password, incoming_mail_use_ssl,
                    webmail_url, company_name, auto_reply_enabled,
                    confirmation_subject, confirmation_body,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                1,  # workspace_id
                'mail.kyotech.co.za',
                587,
                'support@kyotech.co.za',
                'r64Er4hTWf',
                'support@kyotech.co.za',
                'KyoTech Support',
                1,
                'POP3',
                'mail.kyotech.co.za',
                110,
                'support@kyotech.co.za',
                'r64Er4hTWf',
                0,
                'www.kyotech.co.za/webmail',
                'KyoTech Support',
                1,
                'Ticket Confirmation - #{ticket_number}',
                '''Dear {guest_name} {guest_surname},

Thank you for contacting us. Your support ticket has been successfully created.

Ticket Details:
--------------
Ticket Number: {ticket_number}
Subject: {subject}
Status: Open
Priority: {priority}

Our team will review your request and someone will assist you as soon as possible.

You can reference your ticket number {ticket_number} in any future communication.

Best regards,
{company_name} Support Team

---
This is an automated message. Please do not reply to this email.''',
                datetime.utcnow().isoformat(),
                datetime.utcnow().isoformat()
            ))
        
        conn.commit()
        
        # Verify
        cursor.execute("""
            SELECT smtp_host, smtp_port, smtp_username, smtp_from_email, 
                   auto_reply_enabled, company_name
            FROM emailsettings WHERE workspace_id = 1
        """)
        settings = cursor.fetchone()
        
        print("\n[2] Email settings configured:")
        print(f"    SMTP Host: {settings[0]}")
        print(f"    SMTP Port: {settings[1]}")
        print(f"    Username: {settings[2]}")
        print(f"    From Email: {settings[3]}")
        print(f"    Auto Reply: {'Enabled' if settings[4] else 'Disabled'}")
        print(f"    Company Name: {settings[5]}")
        
        print("\n✅ SUCCESS: Email settings configured!")
        print("\n" + "=" * 60)
        print("Email confirmations will now be sent to guests")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    configure_email()
