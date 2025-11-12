# Email-to-Ticket System Setup Guide

## Overview
The Email-to-Ticket system automatically converts incoming support emails into tickets in your CRM. When a client sends an email to your support address, it automatically creates a ticket with proper priority, category, and assignment.

## Features
- ✅ **Automatic Ticket Creation** - Emails become tickets instantly
- ✅ **Smart Priority Detection** - Keywords trigger urgent/high/medium priority
- ✅ **Auto-Categorization** - Detects bugs, features, billing, or support requests
- ✅ **User Matching** - Matches email addresses to existing workspace users
- ✅ **External User Support** - Handles emails from clients not in the system
- ✅ **Clean Email Bodies** - Removes signatures and quoted replies
- ✅ **Attachment Support** - Preserves email attachments (future enhancement)
- ✅ **Background Scheduler** - Checks for new emails every 5 minutes
- ✅ **Manual Trigger** - Admins can manually check emails via UI button

---

## Setup Instructions

### 1. Get Email Credentials

#### For Gmail:
1. Go to your Google Account: https://myaccount.google.com
2. Enable 2-Factor Authentication (if not already enabled)
3. Go to **Security** → **2-Step Verification** → **App passwords**
4. Generate a new App Password:
   - App: Mail
   - Device: Other (Custom name) → "CRM Support Tickets"
5. Copy the 16-character password (e.g., `abcd efgh ijkl mnop`)

#### For Office 365/Outlook:
- Use your regular email and password
- IMAP must be enabled in Outlook settings
- Server: `outlook.office365.com`

#### For Yahoo Mail:
- Generate an App Password at https://login.yahoo.com/account/security
- Server: `imap.mail.yahoo.com`

---

### 2. Configure Email Settings

Edit `app/core/secrets_local.py` (create from `secrets_example.py` if it doesn't exist):

```python
# Email-to-Ticket Configuration
SUPPORT_EMAIL_IMAP_SERVER = "imap.gmail.com"  # Gmail IMAP server
SUPPORT_EMAIL_ADDRESS = "support@yourdomain.com"  # Your support email
SUPPORT_EMAIL_PASSWORD = "abcd efgh ijkl mnop"  # App password (no spaces)
SUPPORT_EMAIL_DEFAULT_ASSIGNED_TO = 1  # User ID to assign tickets to (or None)
EMAIL_CHECK_INTERVAL = 300  # Check every 5 minutes (300 seconds)
```

**Important:** 
- Replace `support@yourdomain.com` with your actual support email
- Replace the password with your App Password (remove spaces: `abcdefghijklmnop`)
- Set `SUPPORT_EMAIL_DEFAULT_ASSIGNED_TO` to your support lead's user ID

---

### 3. IMAP Server Settings

| Email Provider | IMAP Server | Port |
|---------------|-------------|------|
| Gmail | `imap.gmail.com` | 993 |
| Office 365 | `outlook.office365.com` | 993 |
| Yahoo | `imap.mail.yahoo.com` | 993 |
| Custom Domain (Gmail) | `imap.gmail.com` | 993 |
| Custom Domain (Office 365) | `outlook.office365.com` | 993 |

---

### 4. Enable Background Email Checking

The system automatically checks for new emails in the background. The scheduler starts when the server starts.

**Check Status:**
- Background task runs every 5 minutes (configurable via `EMAIL_CHECK_INTERVAL`)
- Console logs show: `[Email-to-Ticket] Scheduler started (checking every 300s)`
- When emails are processed: `[2025-11-05 18:30:00] Created 3 tickets from emails`

---

### 5. Manual Email Check (Admin Feature)

Admins can manually trigger email checking:

1. Go to **Tickets** page
2. Click **"Check Emails"** button (green button, admin-only)
3. System fetches unread emails and creates tickets
4. Success message shows how many tickets were created

---

## How It Works

### Email Processing Flow:

1. **Email Received** → Client sends email to `support@yourdomain.com`
2. **System Checks** → Background task checks IMAP inbox every 5 minutes
3. **Email Parsing** → Extracts subject, body, sender, attachments
4. **User Matching** → Checks if sender email exists in workspace
5. **Priority Detection** → Scans for keywords:
   - **Urgent**: "urgent", "emergency", "critical", "down", "not working"
   - **High**: "important", "high priority", "broken", "error"
   - **Medium**: Default for normal emails
6. **Category Detection**:
   - **Bug**: "bug", "error", "broken", "crash"
   - **Feature**: "feature", "request", "enhancement"
   - **Billing**: "billing", "invoice", "payment"
   - **Support**: Everything else
7. **Ticket Creation** → Creates ticket with auto-generated number (TKT-2025-00001)
8. **Assignment** → Assigns to default user (configurable)
9. **Notification** → Notifies assigned user
10. **Mark Read** → Email marked as read in mailbox

---

## Priority Keywords

### Urgent Priority Triggers:
- urgent
- emergency
- critical
- asap
- immediately
- down
- not working

### High Priority Triggers:
- important
- high priority
- soon
- broken
- error
- bug

### Default: Medium Priority

---

## Category Keywords

### Bug Category:
- bug
- error
- broken
- not working
- crash

### Feature Category:
- feature
- request
- enhancement
- suggest
- add

### Billing Category:
- billing
- invoice
- payment
- charge
- subscription

### Support Category:
- Default for all other emails

---

## External User Emails

When someone **not in your workspace** sends an email:

**Before:**
```
Subject: Website login broken
From: john.doe@clientcompany.com

Hi, I can't login to the website. It keeps saying wrong password.
```

**Created Ticket:**
```
Ticket #TKT-2025-00001
Subject: Website login broken
Priority: High (keyword: "broken")
Category: Bug (keyword: "broken")
Status: Open
Created By: External User

Description:
[External Email from John Doe <john.doe@clientcompany.com>]

Hi, I can't login to the website. It keeps saying wrong password.
```

---

## Email Cleaning

The system automatically cleans emails:

✅ **Removes:**
- Email signatures ("--", "Sent from iPhone")
- Quoted replies (lines starting with `>`)
- Common email footers

✅ **Preserves:**
- Original message content
- Formatting (paragraphs, line breaks)
- Important context

---

## Testing

### Test Email-to-Ticket:

1. **Configure Settings** (secrets_local.py)
2. **Restart Server** to load new config
3. **Send Test Email** to your support address:
   ```
   To: support@yourdomain.com
   Subject: URGENT: Test ticket creation
   Body: This is a test email to verify ticket creation works.
   ```
4. **Wait 5 Minutes** (or click "Check Emails" button)
5. **Verify Ticket Created** in Tickets list
6. **Check Priority** (should be "Urgent" due to keyword)

---

## Troubleshooting

### Issue: "Email-to-ticket not configured"
**Solution:** 
- Verify `secrets_local.py` exists in `app/core/`
- Check all required variables are set
- Restart server

### Issue: "Failed to connect to email"
**Solution:**
- Verify IMAP server address is correct
- Check email and password are correct
- For Gmail: Ensure App Password is used (not regular password)
- Check 2FA is enabled (Gmail requirement)

### Issue: No tickets being created
**Solution:**
- Check server console for error messages
- Verify emails are unread in your inbox
- Test with manual "Check Emails" button
- Check background scheduler started: Look for `[Email-to-Ticket] Scheduler started`

### Issue: Wrong priority/category assigned
**Solution:**
- Check email content for keywords
- Priority/category based on subject + body content
- You can manually change priority/category after creation

---

## Security Notes

⚠️ **Important:**
- Never commit `secrets_local.py` to git (already in `.gitignore`)
- Use App Passwords, not your main email password
- Only admins can manually trigger email checks
- Emails are marked as read after processing (no duplicates)

---

## Advanced Configuration

### Change Check Interval:
```python
EMAIL_CHECK_INTERVAL = 600  # Check every 10 minutes
EMAIL_CHECK_INTERVAL = 180  # Check every 3 minutes
```

### Disable Auto-Assignment:
```python
SUPPORT_EMAIL_DEFAULT_ASSIGNED_TO = None  # Leaves tickets unassigned
```

### Process More Emails Per Check:
Edit `app/core/email_ticket.py`:
```python
emails = service.fetch_unread_emails(limit=20)  # Default is 10
```

---

## Future Enhancements

Planned features:
- [ ] Auto-reply confirmation email to sender
- [ ] Attachment file downloads and storage
- [ ] Email thread tracking (link replies to existing tickets)
- [ ] Custom priority rules per workspace
- [ ] Spam filtering
- [ ] Support multiple email addresses
- [ ] Email templates for responses

---

## Support

If you encounter issues:
1. Check server console logs for errors
2. Verify email credentials are correct
3. Test manual email check first
4. Check IMAP is enabled in your email provider
5. Review the configuration in `secrets_local.py`

---

**Last Updated:** November 5, 2025  
**System Version:** CRM Backend v2.0
