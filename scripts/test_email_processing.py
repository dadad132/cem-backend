"""
Test Email Processing Script
Check email configuration and manually process emails
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.core.database import async_session_factory, init_models
from app.models.email_settings import EmailSettings
from app.models.project import Project
from app.core.email_to_ticket_v2 import process_workspace_emails


async def test_email_configuration():
    """Test and display email configuration"""
    print("=" * 70)
    print("EMAIL CONFIGURATION TEST")
    print("=" * 70)
    
    async with async_session_factory() as db:
        # Get email settings
        result = await db.execute(select(EmailSettings))
        settings = result.scalar_one_or_none()
        
        if not settings:
            print("\n[ERROR] NO EMAIL SETTINGS FOUND!")
            print("\nYou need to configure email settings first.")
            print("Run: python configure_kyotech_email.py")
            return False
        
        print(f"\n[OK] Email settings found for workspace {settings.workspace_id}")
        print(f"\nSMTP Configuration:")
        print(f"  Host: {settings.smtp_host}:{settings.smtp_port}")
        print(f"  From: {settings.smtp_from_email}")
        print(f"  TLS: {'Yes' if settings.smtp_use_tls else 'No'}")
        
        print(f"\nIncoming Mail Configuration:")
        print(f"  Type: {settings.incoming_mail_type or 'Not set'}")
        print(f"  Host: {settings.incoming_mail_host or 'Not set'}")
        print(f"  Port: {settings.incoming_mail_port or 'Not set'}")
        print(f"  Username: {settings.incoming_mail_username or 'Not set'}")
        print(f"  SSL: {'Yes' if settings.incoming_mail_use_ssl else 'No'}")
        
        if not settings.incoming_mail_host:
            print("\n[ERROR] INCOMING MAIL NOT CONFIGURED!")
            print("Email-to-ticket will not work.")
            return False
        
        # Get projects with support emails
        print("\n" + "=" * 70)
        print("PROJECTS WITH SUPPORT EMAILS (Email → Task routing)")
        print("=" * 70)
        
        # Use raw SQL to avoid model schema issues
        from sqlalchemy import text
        result = await db.execute(
            text("SELECT id, name, support_email FROM project WHERE workspace_id = :workspace_id AND support_email IS NOT NULL AND support_email != '' AND is_archived = 0"),
            {"workspace_id": settings.workspace_id}
        )
        projects = result.fetchall()
        
        if projects:
            print(f"\nFound {len(projects)} projects configured for email -> task:")
            for p in projects:
                print(f"\n  Email: {p[2]}")
                print(f"     -> Creates tasks in: {p[1]} (ID: {p[0]})")
        else:
            print("\n[WARNING] NO PROJECTS WITH SUPPORT EMAILS CONFIGURED")
            print("\nEmails will create general support tickets instead of project tasks.")
            print("\nTo route emails to project tasks:")
            print("1. Go to Projects")
            print("2. Edit a project")
            print("3. Set the 'Support Email' field (e.g., project@kyotech.co.za)")
        
        print("\n" + "=" * 70)
        print("EMAIL ROUTING RULES")
        print("=" * 70)
        print("\n1. Email sent to a project support email -> Creates TASK in that project")
        print("2. Email sent to other addresses -> Creates general TICKET")
        print("3. Reply to existing ticket -> Adds COMMENT to that ticket")
        
        return True


async def process_emails_now():
    """Manually trigger email processing"""
    print("\n" + "=" * 70)
    print("PROCESSING EMAILS NOW...")
    print("=" * 70)
    
    async with async_session_factory() as db:
        # Get workspace ID (assume workspace 1)
        result = await db.execute(select(EmailSettings))
        settings = result.scalar_one_or_none()
        
        if not settings:
            print("\n[ERROR] No email settings found!")
            return
        
        try:
            tickets = await process_workspace_emails(db, settings.workspace_id)
            print(f"\n[SUCCESS] Processed {len(tickets)} new emails")
            
            if tickets:
                print("\nCreated tickets/tasks:")
                for ticket in tickets:
                    print(f"  - {ticket.ticket_number}: {ticket.subject[:60]}")
            else:
                print("\nNo new emails found.")
                print("\nPossible reasons:")
                print("  • No unread emails in inbox")
                print("  • All emails already processed")
                print("  • Email connection issue")
                
        except Exception as e:
            print(f"\n[ERROR] {e}")
            import traceback
            traceback.print_exc()
            print("\nTroubleshooting:")
            print("  1. Check email credentials are correct")
            print("  2. Verify incoming mail server settings")
            print("  3. Ensure firewall allows connection")
            print("  4. Check server logs for detailed error")


async def main():
    """Main test function"""
    # Initialize database
    await init_models()
    
    # Test configuration
    config_ok = await test_email_configuration()
    
    if not config_ok:
        print("\n" + "=" * 70)
        print("FIX CONFIGURATION FIRST")
        print("=" * 70)
        return
    
    # Ask if user wants to process emails now
    print("\n" + "=" * 70)
    response = input("\nProcess emails now? (y/n): ").strip().lower()
    
    if response == 'y':
        await process_emails_now()
    else:
        print("\nSkipping email processing.")
        print("\nTo process emails manually, visit:")
        print("  Settings → Email Settings → 'Process Emails Now' button")


if __name__ == "__main__":
    asyncio.run(main())
