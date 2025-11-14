# Project-Specific Support Email Guide

## Overview
Each project/company board can now have its own dedicated support email address. When customers send emails to that address, tickets are automatically created and linked to the correct project.

## How It Works

### Setup (Admin Only)

1. **Go to Projects page** (`/web/projects`)
2. **Click Edit icon** (pencil) on any project
3. **Enter Support Email** (e.g., `support@acmecorp.com`)
4. **Save** the project

The support email will now appear as a blue badge on the project card.

### Email Processing

When an email arrives:

1. **System checks the "To:" address** of the incoming email
2. **Matches it against project support emails** in the database
3. **If match found:**
   - Creates a guest ticket automatically
   - Links ticket to that specific project (`related_project_id`)
   - Adds project name to the notification
   - Shows project badge on ticket page
4. **If no match:**
   - Creates a regular workspace-level guest ticket
   - No project assignment

### Ticket Display

Tickets linked to projects show:
- **Blue project badge** next to the title with folder icon
- **Clickable link** to the project
- **Project name in history**: "Ticket created from email → Project: [Name]"
- **Project name in notification**: "New ticket for [Project] from email #TKT-..."

## Example Workflow

### Company A: ACME Corp
- Project Name: "ACME Corporation"
- Support Email: `support@acmecorp.com`
- Client sends email to `support@acmecorp.com`
- ✅ Ticket auto-created and linked to ACME Corporation project

### Company B: TechStart
- Project Name: "TechStart Inc"
- Support Email: `help@techstart.io`
- Client sends email to `help@techstart.io`
- ✅ Ticket auto-created and linked to TechStart Inc project

### General Support
- Client sends email to your main email (configured in Email Settings)
- Email doesn't match any project support email
- ✅ Ticket created as general workspace ticket (no project link)

## Email Configuration Requirements

Your email settings must be configured for this to work:
1. **Email Settings** page must have IMAP/POP3 configured
2. **"Check Emails" button** or automated checking must be running
3. Email server must deliver emails to the configured mailbox

## Benefits

✅ **Automatic Organization** - Tickets go to the right project/company board
✅ **Multi-Company Support** - Each company has its own email address  
✅ **Easy Tracking** - See which project a ticket belongs to at a glance
✅ **Better Notifications** - Admins see project name in alerts
✅ **Professional** - Give each client their own support address

## Technical Details

### Database
- Field: `project.support_email` (TEXT, nullable)
- Migration: `migrate_project_support_email.py`
- Index: None (small dataset)

### Email Processing
- Extracts "To:" header from email
- Lowercases and strips whitespace
- Queries: `Project.support_email = to_email AND workspace_id AND is_archived=False`
- Passes project to ticket creation function

### Ticket Linking
- Field: `ticket.related_project_id` (already existed)
- Now populated automatically for email-created tickets
- Can still be null for general tickets

## Tips

1. **Use unique emails** - Each project should have a different support email
2. **Keep projects active** - Archived projects won't match (prevents old tickets)
3. **Test first** - Send a test email to verify it works
4. **Check spam** - Make sure your email server isn't filtering the emails
5. **Email forwarding** - You can forward multiple aliases to one mailbox if needed

## Troubleshooting

**Email doesn't create ticket in project:**
- Check if support_email is set correctly (case-sensitive match)
- Verify email was sent to exact address in project settings
- Check email processing logs (server console) for debug messages
- Ensure project is not archived

**Ticket created but not linked to project:**
- Email "To:" address doesn't match any project's support_email
- Check for typos in email address
- Verify email server is delivering with correct headers

**Can't set support email:**
- Only admins can edit projects
- Make sure you're logged in as admin
- Migration must be run first: `python migrate_project_support_email.py`
