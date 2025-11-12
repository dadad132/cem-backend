# Email-to-Ticket System

## Overview
The email-to-ticket system automatically converts support emails into tickets. Clients can email your support address, and the system will:
- Create a ticket automatically
- Send an auto-reply with the ticket number
- Track replies as comments on the ticket
- Notify clients when staff respond

## Features

### ✅ Automatic Ticket Creation
- Emails sent to support@ automatically create tickets
- Ticket number generated (e.g., TKT-2024-00001)
- Default priority: Medium
- Default category: Support

### ✅ Email Thread Tracking
- Clients receive ticket number in auto-reply
- Replies with ticket number become comments
- Full email conversation tracked in ticket

### ✅ Two-Way Communication
- Staff comments sent as email to client
- Client email replies added as ticket comments
- Seamless back-and-forth conversation

### ✅ Smart Email Parsing
- Extracts subject and body
- Removes email signatures
- Filters reply chains
- Identifies ticket numbers in subject

## Quick Setup (Gmail)

### 1. Enable IMAP in Gmail
```
Gmail Settings → Forwarding and POP/IMAP → Enable IMAP
```

### 2. Create App Password
```
1. Go to: https://myaccount.google.com/apppasswords
2. Select: Mail + Your Device
3. Copy the 16-character password
```

### 3. Update .env File
```bash
# Enable the feature
EMAIL_TO_TICKET_ENABLED=true
EMAIL_TO_TICKET_CHECK_INTERVAL=300

# Support email
SUPPORT_EMAIL=support@yourdomain.com

# IMAP settings (receiving)
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USERNAME=support@yourdomain.com
IMAP_PASSWORD=your-app-password-here
IMAP_USE_SSL=true

# SMTP settings (sending - should already be configured)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=support@yourdomain.com
SMTP_PASSWORD=your-app-password-here
SMTP_FROM=support@yourdomain.com
SMTP_USE_TLS=true
```

### 4. Restart Server
```bash
# Stop current server (CTRL+C)
python start_server.py

# You should see:
# [background-tasks] ✓ Email-to-ticket task scheduled
```

## How It Works

### Workflow Example

**1. Client Sends Email**
```
To: support@yourdomain.com
Subject: Can't login to account
Body: I forgot my password and the reset link isn't working...
```

**2. System Creates Ticket**
- Ticket #TKT-2024-00123 created
- Status: Open
- Priority: Medium
- Category: Support

**3. Client Receives Auto-Reply**
```
Subject: [TKT-2024-00123] Can't login to account
Body: 
Thank you for contacting support!
Your ticket has been created with the following details:
- Ticket Number: TKT-2024-00123
- Subject: Can't login to account
- Status: Open

We'll review your request and get back to you soon.
Please keep the ticket number in the subject line when replying.
```

**4. Staff Reviews & Responds**
- Staff views ticket in web interface
- Adds comment: "I've reset your password. Check your email."
- Comment automatically emailed to client

**5. Client Receives Email**
```
Subject: [TKT-2024-00123] New comment on your ticket
Body:
John Smith commented on your ticket:

Can't login to account

"I've reset your password. Check your email."

[View Ticket Button]
```

**6. Client Replies**
```
Subject: Re: [TKT-2024-00123] Can't login to account
Body: Thanks! I was able to login successfully.
```

**7. Reply Added to Ticket**
- Reply appears as comment in ticket
- Staff sees update in web interface
- Can mark ticket as resolved

## Configuration Options

| Setting | Default | Description |
|---------|---------|-------------|
| `EMAIL_TO_TICKET_ENABLED` | false | Enable/disable the feature |
| `EMAIL_TO_TICKET_CHECK_INTERVAL` | 300 | Check inbox every N seconds (5 min) |
| `SUPPORT_EMAIL` | support@example.com | Your support email address |
| `IMAP_HOST` | - | IMAP server (e.g., imap.gmail.com) |
| `IMAP_PORT` | 993 | IMAP port (993 for SSL) |
| `IMAP_USERNAME` | - | Email account username |
| `IMAP_PASSWORD` | - | Email account password |
| `IMAP_USE_SSL` | true | Use SSL for IMAP connection |

## Other Email Providers

### Microsoft 365 / Outlook
```bash
IMAP_HOST=outlook.office365.com
IMAP_PORT=993
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
```

### Yahoo Mail
```bash
IMAP_HOST=imap.mail.yahoo.com
IMAP_PORT=993
SMTP_HOST=smtp.mail.yahoo.com
SMTP_PORT=587
```

### Custom Provider
Contact your email provider for IMAP/SMTP settings.

## Monitoring

### Check Background Task Status
```bash
# Server logs show:
[background-tasks] Starting email-to-ticket checker (interval: 300s)
[email-to-ticket] Processing 3 new emails...
[email-to-ticket] ✓ Created ticket TKT-2024-00123 from client@example.com
[email-to-ticket] ✓ Added comment to ticket TKT-2024-00122
[email-to-ticket] ✓ Processed 3 emails
```

### Common Log Messages
- `Email-to-ticket is disabled` - Feature not enabled in .env
- `IMAP not configured` - Missing IMAP settings
- `No unread emails` - Inbox is empty
- `Processing N new emails...` - Working correctly
- `✓ Created ticket` - New ticket created
- `✓ Added comment` - Reply processed

## Troubleshooting

### Background Task Not Starting
**Symptom:** No `[background-tasks]` messages in logs

**Solutions:**
1. Check `EMAIL_TO_TICKET_ENABLED=true` in .env
2. Restart server completely
3. Check for startup errors in logs

### Cannot Connect to IMAP
**Symptom:** `Error connecting to inbox: ...`

**Solutions:**
1. Verify IMAP_HOST and IMAP_PORT
2. Check IMAP is enabled in email settings
3. Verify credentials (username/password)
4. Try connecting with email client (Thunderbird, Outlook)
5. Check firewall isn't blocking port 993

### Emails Not Creating Tickets
**Symptom:** Emails arrive but no tickets created

**Solutions:**
1. Ensure emails are **unread** in inbox
2. Check server logs for errors
3. Verify IMAP credentials
4. Check email is in INBOX folder (not spam/other)
5. Look for parsing errors in logs

### No Auto-Reply Emails
**Symptom:** Tickets created but client gets no confirmation

**Solutions:**
1. Verify SMTP settings correct
2. Check client's spam folder
3. Look for SMTP errors in logs: `[email]`
4. Test SMTP with: `python app/scripts/send_test_email.py`

### Replies Creating New Tickets
**Symptom:** Each reply becomes a new ticket

**Solutions:**
1. Ensure ticket number in subject: `[TKT-2024-00123]`
2. Check auto-reply includes ticket number
3. Verify subject parsing in logs
4. Test with manual subject: `Re: [TKT-2024-00123] Test`

## Security Considerations

### App Passwords (Recommended)
- Use app-specific passwords, not account password
- Gmail: https://myaccount.google.com/apppasswords
- Outlook: https://account.live.com/proofs/AppPassword

### Environment Variables
- Never commit .env file to Git
- Keep IMAP_PASSWORD secret
- Use different passwords for IMAP and web login

### SSL/TLS
- Always use `IMAP_USE_SSL=true` (port 993)
- Always use `SMTP_USE_TLS=true` (port 587)
- Never use unencrypted connections

### Email Filtering
- Only process emails from INBOX
- Mark processed emails as read
- Consider spam filtering before ticket creation

## Advanced Features

### Internal Comments
Staff can add internal comments (not emailed to client):
- Check "Internal comment" box when adding comment
- Only visible to team members
- Useful for internal notes

### Manual Ticket Assignment
System auto-creates tickets as unassigned. Staff can:
- Assign to team member via dropdown
- Team member receives notification
- Client still receives updates

### Priority & Category
Default: Medium priority, Support category
Staff can change after creation:
- Priority: Low, Medium, High, Urgent
- Category: Support, Bug, Feature, Billing, General

## Testing

### Test Email Creation
1. Send email to support address
2. Wait up to 5 minutes (check interval)
3. Check server logs for processing messages
4. Verify ticket created in web interface
5. Check client received auto-reply

### Test Reply Threading
1. Reply to auto-reply email
2. Keep ticket number in subject
3. Wait for processing
4. Check comment appears in ticket
5. Verify no new ticket created

### Test Staff Notifications
1. Add comment to ticket in web interface
2. Check client's email inbox
3. Verify email contains comment text
4. Test reply-to functionality

## Performance

### Recommended Settings
- Small team (<10 users): Check every 5 minutes (300s)
- Medium team (10-50 users): Check every 2 minutes (120s)
- Large team (>50 users): Check every 1 minute (60s)

### Resource Usage
- Background task uses minimal CPU
- IMAP connection only during checks
- Email parsing is lightweight
- No impact on web interface performance

## Future Enhancements

Planned features:
- [ ] Attachment support (files in emails → ticket attachments)
- [ ] Email templates for different ticket types
- [ ] Custom auto-reply messages
- [ ] SLA tracking via email
- [ ] Email-based ticket closing
- [ ] Multi-language support
- [ ] Email signature extraction
- [ ] Spam detection
- [ ] Email rules and filtering

## Support

If you encounter issues:
1. Check this guide's Troubleshooting section
2. Review server logs for error messages
3. Test email settings with standalone client
4. Verify all .env settings are correct
5. Try increasing check interval temporarily

## See Also
- `EMAIL_TO_TICKET_SETUP.env` - Example configuration
- `app/core/email_to_ticket.py` - Source code
- `app/core/background_tasks.py` - Task scheduler
- `app/core/email.py` - Email sending
