"""
Email-to-Ticket Service
Automatically creates tickets from incoming emails
"""

import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr
import re
from datetime import datetime
from typing import Optional, List, Tuple
import os
from sqlmodel import Session, select
from app.models.ticket import Ticket, TicketComment, TicketAttachment, TicketHistory
from app.models.user import User
from app.models.notification import Notification


class EmailTicketService:
    """Service to process emails and create tickets"""
    
    def __init__(
        self,
        imap_server: str,
        email_address: str,
        email_password: str,
        workspace_id: int,
        default_assigned_to: Optional[int] = None
    ):
        self.imap_server = imap_server
        self.email_address = email_address
        self.email_password = email_password
        self.workspace_id = workspace_id
        self.default_assigned_to = default_assigned_to
        
    def connect(self) -> imaplib.IMAP4_SSL:
        """Connect to IMAP server"""
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server)
            mail.login(self.email_address, self.email_password)
            return mail
        except Exception as e:
            print(f"Failed to connect to email: {e}")
            raise
    
    def decode_email_header(self, header: str) -> str:
        """Decode email header (handles encoding)"""
        if not header:
            return ""
        
        decoded_parts = decode_header(header)
        decoded_string = ""
        
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                decoded_string += part.decode(encoding or 'utf-8', errors='ignore')
            else:
                decoded_string += part
        
        return decoded_string
    
    def extract_email_address(self, from_header: str) -> Tuple[str, str]:
        """Extract name and email from 'From' header"""
        name, email_addr = parseaddr(from_header)
        return name, email_addr.lower()
    
    def clean_email_body(self, body: str) -> str:
        """Clean email body (remove signatures, quoted replies, etc.)"""
        # Remove common email signatures
        lines = body.split('\n')
        cleaned_lines = []
        
        signature_markers = [
            '-- ',
            '___________',
            'Sent from',
            'Get Outlook',
            'Sent from my iPhone',
            'Sent from my Android'
        ]
        
        # Remove quoted replies (lines starting with >)
        for line in lines:
            # Stop at signature markers
            if any(line.strip().startswith(marker) for marker in signature_markers):
                break
            # Skip quoted replies
            if line.strip().startswith('>'):
                continue
            cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines).strip()
    
    def determine_priority(self, subject: str, body: str) -> str:
        """Auto-detect priority from email content"""
        content = (subject + ' ' + body).lower()
        
        urgent_keywords = ['urgent', 'emergency', 'critical', 'asap', 'immediately', 'down', 'not working']
        high_keywords = ['important', 'high priority', 'soon', 'broken', 'error', 'bug']
        
        if any(keyword in content for keyword in urgent_keywords):
            return 'urgent'
        elif any(keyword in content for keyword in high_keywords):
            return 'high'
        else:
            return 'medium'
    
    def determine_category(self, subject: str, body: str) -> str:
        """Auto-detect category from email content"""
        content = (subject + ' ' + body).lower()
        
        if any(word in content for word in ['bug', 'error', 'broken', 'not working', 'crash']):
            return 'bug'
        elif any(word in content for word in ['feature', 'request', 'enhancement', 'suggest', 'add']):
            return 'feature'
        elif any(word in content for word in ['billing', 'invoice', 'payment', 'charge', 'subscription']):
            return 'billing'
        else:
            return 'support'
    
    async def find_or_create_user(self, db: Session, email_addr: str, name: str) -> Optional[User]:
        """Find existing user by email or return None (external user)"""
        # Try to find user by email
        result = await db.execute(
            select(User).where(
                User.email == email_addr,
                User.workspace_id == self.workspace_id
            )
        )
        user = result.scalar_one_or_none()
        return user
    
    async def create_ticket_from_email(
        self,
        db: Session,
        subject: str,
        body: str,
        from_name: str,
        from_email: str,
        attachments: List[dict] = None
    ) -> Ticket:
        """Create a ticket from email data"""
        
        # Find user or use external email
        user = await self.find_or_create_user(db, from_email, from_name)
        created_by_id = user.id if user else None
        
        # Clean and prepare data
        cleaned_body = self.clean_email_body(body)
        priority = self.determine_priority(subject, body)
        category = self.determine_category(subject, body)
        
        # Generate ticket number
        year = datetime.utcnow().year
        result = await db.execute(
            select(Ticket).where(Ticket.workspace_id == self.workspace_id)
        )
        ticket_count = len(result.scalars().all()) + 1
        ticket_number = f"TKT-{year}-{ticket_count:05d}"
        
        # Create description with sender info if external
        description = cleaned_body
        if not user:
            description = f"[External Email from {from_name} <{from_email}>]\n\n{cleaned_body}"
        
        # Create ticket
        ticket = Ticket(
            ticket_number=ticket_number,
            subject=subject,
            description=description,
            priority=priority,
            category=category,
            status='open',
            assigned_to_id=self.default_assigned_to,
            created_by_id=created_by_id,
            workspace_id=self.workspace_id
        )
        db.add(ticket)
        await db.flush()
        
        # Create history
        history = TicketHistory(
            ticket_id=ticket.id,
            user_id=created_by_id,
            action='created',
            new_value=f'Created from email: {from_email}'
        )
        db.add(history)
        
        # Handle attachments
        if attachments:
            for attachment in attachments:
                ticket_attachment = TicketAttachment(
                    ticket_id=ticket.id,
                    filename=attachment['filename'],
                    file_path=attachment['file_path'],
                    file_size=attachment['file_size'],
                    content_type=attachment['content_type']
                )
                db.add(ticket_attachment)
        
        # Notify assigned user
        if self.default_assigned_to:
            notification = Notification(
                user_id=self.default_assigned_to,
                type='ticket',
                message=f'New ticket from email: {ticket_number} - {subject}',
                url=f'/web/tickets/{ticket.id}',
                related_id=ticket.id
            )
            db.add(notification)
        
        await db.commit()
        await db.refresh(ticket)
        
        return ticket
    
    def fetch_unread_emails(self, folder: str = "INBOX", limit: int = 10) -> List[dict]:
        """Fetch unread emails from mailbox"""
        mail = self.connect()
        
        try:
            # Select mailbox
            mail.select(folder)
            
            # Search for unread emails
            status, messages = mail.search(None, 'UNSEEN')
            
            if status != 'OK':
                return []
            
            email_ids = messages[0].split()
            emails = []
            
            # Process emails (limit to avoid overload)
            for email_id in email_ids[-limit:]:
                try:
                    # Fetch email
                    status, msg_data = mail.fetch(email_id, '(RFC822)')
                    
                    if status != 'OK':
                        continue
                    
                    # Parse email
                    raw_email = msg_data[0][1]
                    email_message = email.message_from_bytes(raw_email)
                    
                    # Extract headers
                    subject = self.decode_email_header(email_message.get('Subject', ''))
                    from_header = email_message.get('From', '')
                    from_name, from_email = self.extract_email_address(from_header)
                    date = email_message.get('Date', '')
                    
                    # Extract body
                    body = ""
                    attachments = []
                    
                    if email_message.is_multipart():
                        for part in email_message.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition", ""))
                            
                            # Get email body
                            if content_type == "text/plain" and "attachment" not in content_disposition:
                                try:
                                    body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                except:
                                    pass
                            
                            # Get attachments info (don't download yet)
                            elif "attachment" in content_disposition:
                                filename = part.get_filename()
                                if filename:
                                    attachments.append({
                                        'filename': self.decode_email_header(filename),
                                        'content_type': content_type,
                                        'size': len(part.get_payload(decode=True) or b'')
                                    })
                    else:
                        # Simple email
                        try:
                            body = email_message.get_payload(decode=True).decode('utf-8', errors='ignore')
                        except:
                            body = str(email_message.get_payload())
                    
                    emails.append({
                        'id': email_id.decode(),
                        'subject': subject,
                        'from_name': from_name,
                        'from_email': from_email,
                        'date': date,
                        'body': body,
                        'attachments': attachments
                    })
                    
                except Exception as e:
                    print(f"Error processing email {email_id}: {e}")
                    continue
            
            return emails
            
        finally:
            mail.logout()
    
    def mark_as_read(self, email_id: str, folder: str = "INBOX"):
        """Mark email as read after processing"""
        mail = self.connect()
        try:
            mail.select(folder)
            mail.store(email_id.encode(), '+FLAGS', '\\Seen')
        finally:
            mail.logout()


async def process_emails_to_tickets(db: Session, workspace_id: int, config: dict):
    """
    Main function to process incoming emails and create tickets
    
    config should contain:
    - imap_server: str (e.g., 'imap.gmail.com')
    - email_address: str
    - email_password: str (app password if 2FA enabled)
    - default_assigned_to: Optional[int]
    """
    
    service = EmailTicketService(
        imap_server=config['imap_server'],
        email_address=config['email_address'],
        email_password=config['email_password'],
        workspace_id=workspace_id,
        default_assigned_to=config.get('default_assigned_to')
    )
    
    # Fetch unread emails
    emails = service.fetch_unread_emails(limit=10)
    
    print(f"Found {len(emails)} unread emails to process")
    
    created_tickets = []
    
    for email_data in emails:
        try:
            # Create ticket from email
            ticket = await service.create_ticket_from_email(
                db=db,
                subject=email_data['subject'],
                body=email_data['body'],
                from_name=email_data['from_name'],
                from_email=email_data['from_email'],
                attachments=email_data.get('attachments', [])
            )
            
            created_tickets.append(ticket)
            print(f"✓ Created ticket {ticket.ticket_number} from {email_data['from_email']}")
            
            # Mark as read
            service.mark_as_read(email_data['id'])
            
        except Exception as e:
            print(f"✗ Failed to create ticket from email: {e}")
            continue
    
    return created_tickets
