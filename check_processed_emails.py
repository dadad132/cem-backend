import asyncio
from app.core.database import get_session
from app.models import ProcessedMail
from sqlmodel import select

async def check():
    async for db in get_session():
        # Get last 20 processed emails
        result = await db.execute(
            select(ProcessedMail)
            .order_by(ProcessedMail.processed_at.desc())
            .limit(20)
        )
        emails = result.scalars().all()
        
        if emails:
            print(f"Found {len(emails)} processed emails:\n")
            for i, email in enumerate(emails, 1):
                print(f"{i}. Subject: {email.subject}")
                print(f"   From: {email.sender_email}")
                print(f"   Ticket ID: {email.ticket_id}")
                print(f"   Processed: {email.processed_at}")
                print(f"   Message ID: {email.message_id[:50]}...")
                print()
        else:
            print("No emails have been processed yet.")
            print("\nPossible reasons:")
            print("1. Email settings not configured")
            print("2. No emails in the inbox")
            print("3. Server hasn't checked yet (checks every 5 minutes)")
        break

asyncio.run(check())
