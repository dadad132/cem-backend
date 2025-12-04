"""
Email-to-Ticket Service V2
IMAP-based email processing, uses database settings, keeps emails on server
"""

import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr
import re
import logging
from datetime import datetime, date, timezone, timedelta
from typing import Optional, List, Tuple
from sqlmodel import Session, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.ticket import Ticket, TicketHistory, TicketComment
from app.models.user import User
from app.models.notification import Notification
from app.models.email_settings import EmailSettings
from app.models.processed_mail import ProcessedMail
from app.models.project import Project
from app.models.task import Task
from app.models.enums import TaskStatus, TaskPriority

# Setup logger
logger = logging.getLogger(__name__)

# Timezone offset (UTC+2 for South Africa)
LOCAL_TZ_OFFSET = timedelta(hours=2)

def get_local_time() -> datetime:
    """Get current time in local timezone (UTC+2)"""
    return datetime.now(timezone(LOCAL_TZ_OFFSET))


class EmailToTicketService:
    """Service to process emails from IMAP and create tickets"""
    
    def __init__(self, email_settings: EmailSettings, workspace_id: int):
        self.settings = email_settings
        self.workspace_id = workspace_id
        
    def connect_imap(self):
        """Connect to IMAP server"""
        try:
            if self.settings.incoming_mail_use_ssl:
                mail = imaplib.IMAP4_SSL(
                    self.settings.incoming_mail_host,
                    self.settings.incoming_mail_port or 993
                )
            else:
                mail = imaplib.IMAP4(
                    self.settings.incoming_mail_host,
                    self.settings.incoming_mail_port or 143
                )
            
            mail.login(
                self.settings.incoming_mail_username,
                self.settings.incoming_mail_password
            )
            return mail
        except Exception as e:
            print(f"Failed to connect to IMAP server: {e}")
            raise
    
    def decode_header_value(self, header: str) -> str:
        """Decode email header"""
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
        """Clean email body (remove signatures, quoted replies)"""
        lines = body.split('\n')
        cleaned_lines = []
        
        signature_markers = [
            '-- ', '___________', 'Sent from', 'Get Outlook',
            'Sent from my iPhone', 'Sent from my Android'
        ]
        
        for line in lines:
            if any(line.strip().startswith(marker) for marker in signature_markers):
                break
            if line.strip().startswith('>'):
                continue
            cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines).strip()
    
    def determine_priority(self, subject: str, body: str) -> str:
        """Auto-detect priority from content"""
        content = (subject + ' ' + body).lower()
        
        urgent_keywords = ['urgent', 'emergency', 'critical', 'asap', 'down', 'not working']
        high_keywords = ['important', 'high priority', 'soon', 'broken', 'error']
        
        if any(keyword in content for keyword in urgent_keywords):
            return 'urgent'
        elif any(keyword in content for keyword in high_keywords):
            return 'high'
        else:
            return 'medium'
    
    def analyze_email_for_task(self, subject: str, body: str) -> tuple[str, str]:
        """
        Analyze email to extract concise title (max 3 words) and clean description
        Uses simple NLP to identify key issue from email content
        """
        # Clean the body
        cleaned_body = self.clean_email_body(body)
        
        # Generate short title (max 3 words)
        # Priority order: look for key action words and subjects
        title_keywords = []
        
        # Common IT/support action words
        action_words = ['fix', 'repair', 'install', 'setup', 'configure', 'update', 'upgrade', 
                       'replace', 'check', 'troubleshoot', 'reset', 'restore', 'resolve',
                       'connection', 'issue', 'problem', 'error', 'bug', 'crash', 'slow']
        
        # Technical subjects
        tech_subjects = ['email', 'printer', 'network', 'wifi', 'computer', 'laptop', 'server',
                        'database', 'website', 'application', 'software', 'password', 'access',
                        'login', 'account', 'internet', 'phone', 'mobile', 'vpn']
        
        # Extract from subject first (most concise)
        subject_lower = subject.lower()
        for word in tech_subjects:
            if word in subject_lower:
                title_keywords.append(word.title())
                if len(title_keywords) >= 2:
                    break
        
        for word in action_words:
            if word in subject_lower:
                title_keywords.insert(0, word.title())
                break
        
        # If subject didn't yield enough, check body
        if len(title_keywords) < 2:
            body_lower = cleaned_body.lower()
            for word in tech_subjects:
                if word in body_lower and word.title() not in title_keywords:
                    title_keywords.append(word.title())
                    if len(title_keywords) >= 2:
                        break
        
        # Build title (max 3 words)
        if title_keywords:
            title = ' '.join(title_keywords[:3])
        else:
            # Fallback: use first 3 meaningful words from subject
            words = [w for w in subject.split() if len(w) > 3][:3]
            title = ' '.join(words) if words else 'Support Request'
        
        # Ensure title isn't too long
        if len(title) > 50:
            title = title[:47] + '...'
        
        # Create structured description
        description = f"""📧 Email Request from: {subject}

{cleaned_body}

---
Auto-created from email support request"""
        
        return title, description
    
    def extract_email_body(self, msg) -> str:
        """Extract plain text body from email message, converting HTML if needed"""
        body = ""
        html_body = ""
        
        if msg.is_multipart():
            # Try to get both plain text and HTML versions
            for part in msg.walk():
                content_type = part.get_content_type()
                
                if content_type == "text/plain" and not body:
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or 'utf-8'
                        body = payload.decode(charset, errors='ignore')
                    except:
                        continue
                
                elif content_type == "text/html" and not html_body:
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or 'utf-8'
                        html_body = payload.decode(charset, errors='ignore')
                    except:
                        continue
        else:
            content_type = msg.get_content_type()
            try:
                payload = msg.get_payload(decode=True)
                charset = msg.get_content_charset() or 'utf-8'
                decoded = payload.decode(charset, errors='ignore')
                
                if content_type == "text/html":
                    html_body = decoded
                else:
                    body = decoded
            except:
                body = str(msg.get_payload())
        
        # If we only have HTML, convert it to plain text
        if not body and html_body:
            body = self.html_to_text(html_body)
        elif not body:
            body = "No content"
        
        return self.clean_email_body(body)
    
    def html_to_text(self, html: str) -> str:
        """Convert HTML email to plain text"""
        from html.parser import HTMLParser
        import re
        
        class HTMLToText(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.skip = False
                
            def handle_starttag(self, tag, attrs):
                if tag in ['script', 'style', 'head']:
                    self.skip = True
                elif tag == 'br':
                    self.text.append('\n')
                elif tag == 'p':
                    self.text.append('\n\n')
                elif tag in ['li']:
                    self.text.append('\n• ')
                    
            def handle_endtag(self, tag):
                if tag in ['script', 'style', 'head']:
                    self.skip = False
                elif tag in ['p', 'div', 'tr']:
                    self.text.append('\n')
                    
            def handle_data(self, data):
                if not self.skip:
                    self.text.append(data)
        
        try:
            parser = HTMLToText()
            parser.feed(html)
            text = ''.join(parser.text)
            
            # Clean up extra whitespace
            text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)  # Max 2 consecutive newlines
            text = re.sub(r' +', ' ', text)  # Multiple spaces to single space
            text = text.strip()
            
            return text
        except Exception as e:
            print(f"Error converting HTML to text: {e}")
            # Fallback: strip all HTML tags
            return re.sub(r'<[^>]+>', '', html)
    
    async def is_email_processed(self, db: AsyncSession, message_id: str) -> bool:
        """Check if email was already processed"""
        result = await db.execute(
            select(ProcessedMail).where(
                ProcessedMail.message_id == message_id,
                ProcessedMail.workspace_id == self.workspace_id
            )
        )
        return result.scalar_one_or_none() is not None
    
    async def find_ticket_by_reply(self, db: AsyncSession, in_reply_to: str, references: str) -> Optional[Ticket]:
        """Find ticket from reply headers (In-Reply-To or References)"""
        print(f"[DEBUG] find_ticket_by_reply called with:")
        print(f"[DEBUG]   in_reply_to: '{in_reply_to}'")
        print(f"[DEBUG]   references: '{references}'")
        print(f"[DEBUG]   workspace_id: {self.workspace_id}")
        
        # Try In-Reply-To first
        if in_reply_to:
            print(f"[DEBUG] Searching processedmail for message_id: '{in_reply_to}'")
            result = await db.execute(
                select(ProcessedMail).where(
                    ProcessedMail.message_id == in_reply_to,
                    ProcessedMail.workspace_id == self.workspace_id
                )
            )
            processed = result.scalar_one_or_none()
            print(f"[DEBUG] ProcessedMail result: {processed}")
            if processed and processed.ticket_id:
                print(f"[DEBUG] Found ticket_id: {processed.ticket_id}")
                ticket_result = await db.execute(
                    select(Ticket).where(Ticket.id == processed.ticket_id)
                )
                ticket = ticket_result.scalar_one_or_none()
                print(f"[DEBUG] Returning ticket: {ticket.ticket_number if ticket else None}")
                return ticket
        
        # Try References (can contain multiple message IDs)
        if references:
            print(f"[DEBUG] Trying References header")
            # References format: "<msg1> <msg2> <msg3>"
            ref_ids = references.strip().split()
            print(f"[DEBUG] Parsed reference IDs: {ref_ids}")
            for ref_id in reversed(ref_ids):  # Check from newest to oldest
                # Keep angle brackets for matching
                ref_id = ref_id.strip()
                print(f"[DEBUG] Checking reference: '{ref_id}'")
                result = await db.execute(
                    select(ProcessedMail).where(
                        ProcessedMail.message_id == ref_id,
                        ProcessedMail.workspace_id == self.workspace_id
                    )
                )
                processed = result.scalar_one_or_none()
                if processed and processed.ticket_id:
                    ticket_result = await db.execute(
                        select(Ticket).where(Ticket.id == processed.ticket_id)
                    )
                    ticket = ticket_result.scalar_one_or_none()
                    print(f"[DEBUG] Found ticket via References: {ticket.ticket_number if ticket else None}")
                    return ticket
        
        print(f"[DEBUG] No ticket found via In-Reply-To or References")
        return None
    
    async def find_ticket_by_subject(self, db: AsyncSession, subject: str) -> Optional[Ticket]:
        """
        Fallback: Find ticket by subject line pattern
        Gmail/Outlook include "Re: Ticket #12345" or "Ticket #12345" in subject
        """
        import re
        
        print(f"[DEBUG] Trying to find ticket by subject: '{subject}'")
        
        # Clean up the subject - remove Re:, Fwd:, etc.
        clean_subject = re.sub(r'^(Re:|RE:|Fwd:|FWD:|\[.*?\])\s*', '', subject, flags=re.IGNORECASE).strip()
        print(f"[DEBUG] Cleaned subject: '{clean_subject}'")
        
        # Look for patterns like "Ticket #12345" or "#12345"
        patterns = [
            r'Ticket\s*#?\s*(\d+)',      # "Ticket #12345" or "Ticket 12345"
            r'Re:\s*Ticket\s*#?\s*(\d+)', # "Re: Ticket #12345"
            r'#(\d+)',                     # "#12345" anywhere
            r'\bticket\s*#?\s*(\d+)',      # "ticket 12345" (case insensitive)
            r'\[#(\d+)\]',                 # "[#12345]"
            r'(?:^|\s)(\d{5,})',           # 5+ digit number (likely ticket number)
        ]
        
        # Try on both original and cleaned subject
        for test_subject in [subject, clean_subject]:
            for pattern in patterns:
                match = re.search(pattern, test_subject, re.IGNORECASE)
                if match:
                    ticket_number = match.group(1)
                    print(f"[DEBUG] Found potential ticket number in subject: {ticket_number} (pattern: {pattern})")
                    
                    # Search for ticket by number
                    result = await db.execute(
                        select(Ticket).where(
                            Ticket.ticket_number == ticket_number,
                            Ticket.workspace_id == self.workspace_id
                        )
                    )
                    ticket = result.scalar_one_or_none()
                    if ticket:
                        print(f"[DEBUG] ✅ Found ticket #{ticket.ticket_number} via subject line")
                        return ticket
                    else:
                        print(f"[DEBUG] Pattern matched '{ticket_number}' but no ticket found in database")
        
        print(f"[DEBUG] ❌ No ticket number found in subject")
        return None
    
    async def find_ticket_by_sender(self, db: AsyncSession, sender_email: str) -> Optional[Ticket]:
        """
        Last resort fallback: Find most recent open ticket from this sender
        Only matches if there's exactly ONE open ticket from this email
        """
        print(f"[DEBUG] Trying to find ticket by sender email: '{sender_email}'")
        
        # Search for open tickets from this email (not closed)
        result = await db.execute(
            select(Ticket).where(
                Ticket.guest_email == sender_email,
                Ticket.workspace_id == self.workspace_id,
                Ticket.status.in_(['new', 'open', 'pending', 'in_progress'])
            ).order_by(Ticket.created_at.desc())
        )
        tickets = result.scalars().all()
        
        if len(tickets) == 1:
            # Only auto-match if there's exactly one open ticket
            print(f"[DEBUG] ✅ Found single open ticket #{tickets[0].ticket_number} from sender")
            return tickets[0]
        elif len(tickets) > 1:
            print(f"[DEBUG] Found {len(tickets)} open tickets from sender - ambiguous, creating new ticket")
        else:
            print(f"[DEBUG] No open tickets found from sender")
        
        return None
    
    async def mark_email_processed(
        self, 
        db: AsyncSession, 
        message_id: str, 
        email_from: str, 
        subject: str, 
        ticket_id: int
    ):
        """Mark email as processed"""
        processed = ProcessedMail(
            message_id=message_id,
            email_from=email_from,
            subject=subject,
            ticket_id=ticket_id,
            workspace_id=self.workspace_id,
            processed_at=get_local_time()
        )
        db.add(processed)
        await db.commit()
    
    async def find_project_by_email(self, db: AsyncSession, to_email: str) -> Optional[Project]:
        """Find project by support email address"""
        if not to_email:
            return None
        
        to_email = to_email.lower().strip()
        print(f"[DEBUG] Looking for project with support_email: {to_email}")
        
        result = await db.execute(
            select(Project).where(
                Project.workspace_id == self.workspace_id,
                Project.support_email == to_email,
                Project.is_archived == False
            )
        )
        project = result.scalar_one_or_none()
        
        if project:
            print(f"[DEBUG] Found project: {project.name} (ID: {project.id})")
        else:
            print(f"[DEBUG] No project found for email: {to_email}")
        
        return project
    
    async def create_ticket_from_email(
        self,
        db: AsyncSession,
        sender_name: str,
        sender_email: str,
        subject: str,
        body: str,
        to_email: Optional[str] = None,
        project: Optional[Project] = None
    ) -> Ticket:
        """Create a guest ticket from email"""
        
        # Generate ticket number
        result = await db.execute(
            select(Ticket).where(Ticket.workspace_id == self.workspace_id)
        )
        ticket_count = len(result.all()) + 1
        ticket_number = f"TKT-{datetime.now().year}-{ticket_count:05d}"
        
        # Determine priority
        priority = self.determine_priority(subject, body)
        
        # Create ticket
        ticket = Ticket(
            ticket_number=ticket_number,
            subject=subject[:200],  # Limit subject length
            description=body[:5000],  # Limit body length
            priority=priority,
            status='open',
            category='support',
            workspace_id=self.workspace_id,
            created_by_id=None,  # Guest ticket
            is_guest=True,
            guest_name=sender_name.split()[0] if sender_name else "Unknown",
            guest_surname=sender_name.split()[-1] if sender_name and len(sender_name.split()) > 1 else "",
            guest_email=sender_email,
            guest_phone="",
            guest_company="",
            guest_branch="",
            related_project_id=project.id if project else None,  # Link to project if found
            created_at=get_local_time(),
            updated_at=get_local_time()
        )
        
        db.add(ticket)
        await db.flush()
        
        # Add history entry
        history_comment = f'Ticket created automatically from email: {sender_email}'
        if project:
            history_comment += f' → Project: {project.name}'
        if to_email:
            history_comment += f' (to: {to_email})'
            
        history = TicketHistory(
            ticket_id=ticket.id,
            user_id=None,  # System action
            action='created',
            comment=history_comment,
            created_at=datetime.utcnow()
        )
        db.add(history)
        
        # Notify all admins about new email ticket
        from app.models.notification import Notification
        from app.models.user import User
        from sqlmodel import select as sql_select
        
        admin_users = (await db.execute(
            sql_select(User).where(User.workspace_id == self.workspace_id).where(User.is_admin == True)
        )).scalars().all()
        
        notification_message = f'New ticket from email #{ticket_number}: {subject[:100]}'
        if project:
            notification_message = f'New ticket for {project.name} from email #{ticket_number}: {subject[:100]}'
        
        for admin in admin_users:
            notification = Notification(
                user_id=admin.id,
                type='ticket',
                message=notification_message,
                url=f'/web/tickets/{ticket.id}',
                related_id=ticket.id
            )
            db.add(notification)
        
        await db.commit()
        await db.refresh(ticket)
        
        return ticket
    
    async def create_task_from_email(
        self, 
        db: AsyncSession,
        sender_name: str,
        sender_email: str,
        subject: str,
        body: str,
        project: Project
    ):
        """Create task from email for projects with support emails"""
        from sqlmodel import select as sql_select
        
        try:
            # Analyze email to extract concise title and description
            title, description = self.analyze_email_for_task(subject, body)
            
            # Determine priority using existing method
            priority_str = self.determine_priority(subject, body)
            
            # Map string priority to TaskPriority enum
            priority_map = {
                'urgent': TaskPriority.critical,
                'high': TaskPriority.high,
                'medium': TaskPriority.medium,
                'low': TaskPriority.low
            }
            task_priority = priority_map.get(priority_str, TaskPriority.medium)
            
            # Get project creator as task creator (or first admin if not available)
            creator_id = project.created_by
            if not creator_id:
                # Fallback: use first admin
                admin_query = sql_select(User).where(User.role == 'admin', User.is_active == True)
                result = await db.execute(admin_query)
                admin = result.scalars().first()
                creator_id = admin.id if admin else 1
            
            # Create task with start_date as today, no due_date
            new_task = Task(
                title=title,
                description=description,
                project_id=project.id,
                creator_id=creator_id,
                status=TaskStatus.todo,
                priority=task_priority,
                start_date=date.today(),
                due_date=None  # No deadline as requested
            )
            
            db.add(new_task)
            await db.commit()
            await db.refresh(new_task)
            
            logger.info(f"✅ Created task '{title}' from {sender_email} for project '{project.name}'")
            
            # Notify project members
            from app.models.project_member import ProjectMember
            members_query = sql_select(ProjectMember).where(ProjectMember.project_id == project.id)
            result = await db.execute(members_query)
            members = result.scalars().all()
            
            for member in members:
                notification = Notification(
                    user_id=member.user_id,
                    title=f"New Task: {title}",
                    message=f"Email from {sender_name} created new task in '{project.name}': {subject}",
                    type='info',
                    related_id=new_task.id,
                    related_type='task'
                )
                db.add(notification)
            
            await db.commit()
            
            return new_task
            
        except Exception as e:
            logger.error(f"❌ Error creating task from email: {e}")
            await db.rollback()
            raise
    
    async def add_comment_from_email(
        self,
        db: AsyncSession,
        ticket: Ticket,
        sender_name: str,
        sender_email: str,
        body: str
    ) -> TicketComment:
        """Add a comment to an existing ticket from email reply"""
        
        # Create comment
        comment = TicketComment(
            ticket_id=ticket.id,
            user_id=None,  # Guest comment from email
            content=f"**Email reply from {sender_name} ({sender_email}):**\n\n{body}",
            is_internal=False,
            created_at=get_local_time()
        )
        db.add(comment)
        
        # Update ticket timestamp
        ticket.updated_at = get_local_time()
        
        # Add history entry
        history = TicketHistory(
            ticket_id=ticket.id,
            user_id=None,
            action='comment_added',
            comment=f'Email reply received from {sender_email}',
            created_at=get_local_time()
        )
        db.add(history)
        
        # Notify all users who have tickets assigned (excluding admins)
        from app.models.user import User
        from sqlmodel import select, distinct
        
        # Get all users who have tickets assigned to them, excluding admins
        assigned_users_query = (
            select(distinct(Ticket.assigned_to_id))
            .where(Ticket.assigned_to_id.isnot(None))
            .where(Ticket.workspace_id == ticket.workspace_id)
        )
        assigned_user_ids_result = await db.execute(assigned_users_query)
        assigned_user_ids = [uid for uid in assigned_user_ids_result.scalars().all() if uid]
        
        # Filter out admins
        if assigned_user_ids:
            users_query = (
                select(User)
                .where(User.id.in_(assigned_user_ids))
                .where(User.is_admin == False)
            )
            non_admin_users = (await db.execute(users_query)).scalars().all()
            
            # Create notification for each non-admin user with assigned tickets
            for user in non_admin_users:
                notification = Notification(
                    user_id=user.id,
                    type='email_reply',
                    message=f'📧 Email reply received on ticket #{ticket.ticket_number} from {sender_email}',
                    url=f'/web/tickets/{ticket.id}',
                    related_id=ticket.id
                )
                db.add(notification)
        
        await db.commit()
        await db.refresh(comment)
        
        return comment
    
    async def fetch_imap_emails(self, db: AsyncSession) -> List[Ticket]:
        """Fetch emails from IMAP server and create tickets"""
        tickets_created = []
        
        try:
            mail = self.connect_imap()
            mail.select('INBOX')
            
            # Search for unread emails
            status, messages = mail.search(None, 'UNSEEN')
            email_ids = messages[0].split()
            
            print(f"[IMAP] Found {len(email_ids)} unread messages")
            
            for email_id in email_ids:
                try:
                    # Fetch email
                    status, msg_data = mail.fetch(email_id, '(RFC822)')
                    msg = email.message_from_bytes(msg_data[0][1])
                    
                    # Get message ID
                    message_id = msg.get('Message-ID', f'no-id-{email_id.decode()}')
                    
                    # Check if already processed
                    if await self.is_email_processed(db, message_id):
                        # Mark as read but don't process again
                        mail.store(email_id, '+FLAGS', '\\Seen')
                        continue
                    
                    # Extract email info
                    from_header = msg.get('From', '')
                    sender_name, sender_email = self.extract_email_address(from_header)
                    to_header = msg.get('To', '')
                    _, to_email = self.extract_email_address(to_header)
                    subject = self.decode_header_value(msg.get('Subject', 'No Subject'))
                    body = self.extract_email_body(msg)
                    
                    print(f"\n{'='*80}")
                    print(f"[IMAP] Processing email")
                    print(f"[IMAP] From: {sender_name} <{sender_email}>")
                    print(f"[IMAP] To: {to_email}")
                    print(f"[IMAP] Subject: {subject}")
                    print(f"[IMAP] Message-ID: {message_id}")
                    
                    # Check if this is a reply to an existing ticket
                    # Keep Message-ID with angle brackets for matching
                    in_reply_to = msg.get('In-Reply-To', '').strip()
                    references = msg.get('References', '').strip()
                    
                    print(f"[IMAP] In-Reply-To: '{in_reply_to}'")
                    print(f"[IMAP] References: '{references}'")
                    
                    existing_ticket = await self.find_ticket_by_reply(db, in_reply_to, references)
                    
                    # If not found via headers, try subject line (Gmail/Outlook fallback)
                    if not existing_ticket:
                        print(f"[IMAP] Trying subject line fallback...")
                        existing_ticket = await self.find_ticket_by_subject(db, subject)
                    
                    # If still not found, try by sender email (last resort)
                    if not existing_ticket:
                        print(f"[IMAP] Trying sender email fallback...")
                        existing_ticket = await self.find_ticket_by_sender(db, sender_email)
                    
                    if existing_ticket:
                        print(f"[IMAP] ✅ MATCH FOUND - Adding to ticket #{existing_ticket.ticket_number}")
                    else:
                        print(f"[IMAP] ❌ NO MATCH - Will create new ticket")
                    print(f"{'='*80}\n")
                    
                    # Find project by support email
                    project = await self.find_project_by_email(db, to_email)
                    
                    if existing_ticket:
                        # Add as comment to existing ticket
                        await self.add_comment_from_email(
                            db, existing_ticket, sender_name, sender_email, body
                        )
                        
                        # Mark as processed
                        await self.mark_email_processed(
                            db, message_id, sender_email, subject, existing_ticket.id
                        )
                        
                        # Mark email as read (keeps it on server)
                        mail.store(email_id, '+FLAGS', '\\Seen')
                        
                        print(f"[IMAP] Added comment to ticket {existing_ticket.ticket_number} from {sender_email}")
                    else:
                        # Route based on project support email
                        if project:
                            # Create task for project with support email
                            task = await self.create_task_from_email(
                                db, sender_name, sender_email, subject, body, project
                            )
                            
                            # Mark as processed
                            await self.mark_email_processed(
                                db, message_id, sender_email, subject, task.id
                            )
                            
                            # Mark email as read (keeps it on server)
                            mail.store(email_id, '+FLAGS', '\\Seen')
                            
                            print(f"[IMAP] Created task '{task.title}' for project '{project.name}' from {sender_email}")
                        else:
                            # Create ticket for general support
                            ticket = await self.create_ticket_from_email(
                                db, sender_name, sender_email, subject, body, to_email, None
                            )
                            
                            # Mark as processed
                            await self.mark_email_processed(
                                db, message_id, sender_email, subject, ticket.id
                            )
                            
                            # Mark email as read (keeps it on server)
                            mail.store(email_id, '+FLAGS', '\\Seen')
                            
                            tickets_created.append(ticket)
                            print(f"[IMAP] Created ticket {ticket.ticket_number} from {sender_email}")
                    
                except Exception as e:
                    print(f"[IMAP] Error processing email {email_id}: {e}")
                    continue
            
            mail.close()
            mail.logout()
            
        except Exception as e:
            print(f"[IMAP] Error fetching emails: {e}")
        
        return tickets_created
    
    async def process_emails(self, db: AsyncSession) -> List[Ticket]:
        """Process emails from IMAP server"""
        return await self.fetch_imap_emails(db)


async def process_workspace_emails(db: AsyncSession, workspace_id: int) -> List[Ticket]:
    """
    Process emails for a workspace using its email settings
    
    Args:
        db: Database session
        workspace_id: Workspace ID
        
    Returns:
        List of created tickets
    """
    # Get email settings
    result = await db.execute(
        select(EmailSettings).where(EmailSettings.workspace_id == workspace_id)
    )
    settings = result.scalar_one_or_none()
    
    if not settings:
        print(f"[Email] No email settings found for workspace {workspace_id}")
        return []
    
    if not settings.incoming_mail_host:
        print(f"[Email] Incoming mail not configured for workspace {workspace_id}")
        return []
    
    # Create service and process emails
    service = EmailToTicketService(settings, workspace_id)
    return await service.process_emails(db)
