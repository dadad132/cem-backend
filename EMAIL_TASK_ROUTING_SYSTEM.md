# Email Task/Ticket Routing System

## Overview
The CRM system now features intelligent email routing that automatically creates either **Tasks** or **Tickets** based on whether the recipient email matches a project's support email.

## How It Works

### Email Processing Flow
```
Incoming Email
    ↓
Extract "To:" address
    ↓
Match against project.support_email
    ↓
┌─────────────────────┬─────────────────────┐
│  Match Found        │  No Match           │
│  (Project Email)    │  (General Support)  │
├─────────────────────┼─────────────────────┤
│  CREATE TASK        │  CREATE TICKET      │
│  • Project Board    │  • Support System   │
│  • 3-word title     │  • Full subject     │
│  • Start date only  │  • Standard flow    │
│  • Notify members   │  • Notify admins    │
└─────────────────────┴─────────────────────┘
```

## Task Creation (Projects with Support Email)

When an email is sent to a project's support email:

### Created Item: **Task**
- **Title**: AI-analyzed, max 3 words (e.g., "Network Cable Issue")
- **Description**: Full email content with sender info
- **Start Date**: Date email received
- **Due Date**: None (open-ended)
- **Status**: `todo`
- **Priority**: Auto-detected from content (critical/high/medium/low)
- **Project**: Automatically assigned to matching project
- **Creator**: Project owner or first admin

### Notifications
- All project members receive notification
- Title: "New Task: [task title]"
- Message includes sender name and email subject

### Example
```
Email to: support@acmecorp.com
From: client@customer.com
Subject: Printer not working in office 3

Result:
→ Task created on Acme Corp project board
   Title: "Printer Issue"
   Start: 2024-01-15
   Priority: High
   Description: Full email content
```

## Ticket Creation (General Support)

When an email is sent to the main support address:

### Created Item: **Ticket**
- **Ticket Number**: Auto-generated (TKT-YYYYMMDD-XXXXXX)
- **Subject**: Full email subject
- **Description**: Cleaned email body
- **Priority**: Auto-detected
- **Status**: `open`
- **Related Project**: None

### Notifications
- All admin users receive notification
- Standard ticket notification flow

### Example
```
Email to: support@yourcrm.com
From: random@client.com
Subject: Need help with account

Result:
→ Ticket created in support system
   #TKT-20240115-A1B2C3
   Subject: "Need help with account"
   Status: Open
```

## AI Email Analysis

The system uses intelligent content analysis to extract key information:

### Title Extraction (for Tasks)
1. **Scan for action words**: fix, repair, install, setup, configure, update, etc.
2. **Identify technical subjects**: printer, network, email, computer, software, etc.
3. **Combine into 3-word title**: "Printer Setup Issue"
4. **Fallback**: Use first 3 meaningful words from subject

### Priority Detection
- **Critical**: Keywords like "urgent", "emergency", "critical", "down", "not working"
- **High**: "important", "high priority", "soon", "broken", "error"
- **Medium**: Default for other emails
- **Low**: (can be manually adjusted later)

### Description Formatting
```markdown
📧 Email Request from: [original subject]

[cleaned email body]

---
Auto-created from email support request
```

## Configuration

### Setting Up Project Support Email
1. Go to Projects page
2. Click "Edit" on a project
3. Enter support email address (e.g., `support@company.com`)
4. Save project

### Email Settings (Admin)
- Configure via Settings → Email Settings
- Supports both POP3 and IMAP
- Emails remain on server after processing
- Automatic duplicate detection

## Use Cases

### Use Case 1: Managed Client
**Scenario**: Acme Corp has a support contract
- **Setup**: Set `support@acmecorp.com` on Acme Corp project
- **Result**: Client emails create tasks on project board
- **Benefit**: Integrated with project management, visible to team

### Use Case 2: Ad-hoc Support
**Scenario**: Random customer needs help
- **Setup**: They email general support address
- **Result**: Creates ticket in support system
- **Benefit**: Tracked separately from project work

### Use Case 3: Technician Assignment
**Scenario**: Technician assigned to multiple projects
- **View**: Can filter tickets by their assigned projects
- **Benefit**: See only relevant support requests

## Technical Details

### Modified Files
- `app/core/email_to_ticket_v2.py`:
  - Added `analyze_email_for_task()` method
  - Added `create_task_from_email()` method
  - Updated POP3/IMAP processing to route emails
  - Enhanced priority detection

### Database Changes
- Project model has `support_email` field
- Tasks linked to projects via `project_id`
- Tickets can optionally link via `related_project_id`

### Task Model Fields
```python
Task(
    title: str,              # Max 3 words
    description: str,        # Full email content
    project_id: int,         # Auto-assigned
    creator_id: int,         # Project owner/admin
    status: TaskStatus,      # Always 'todo' initially
    priority: TaskPriority,  # AI-detected
    start_date: date,        # Email received date
    due_date: Optional[date] # Always None for email tasks
)
```

## Benefits

### For Project Managers
- ✅ Client emails automatically become project tasks
- ✅ No manual ticket → task conversion needed
- ✅ Full email context preserved
- ✅ Team visibility into client requests

### For Support Technicians
- ✅ Clear separation: project work vs general support
- ✅ Can filter view by assigned projects
- ✅ Automatic priority assignment
- ✅ Smart title generation (no lengthy subjects)

### For Clients
- ✅ Simply send email to support address
- ✅ Automatic ticket/task creation
- ✅ Replies thread correctly
- ✅ No portal login required

## Monitoring & Logs

The system logs all email processing:
```
[POP3] Created task 'Printer Issue' for project 'Acme Corp' from client@acme.com
[IMAP] Created ticket TKT-20240115-A1B2C3 from random@client.com
```

Check logs for:
- Email fetching status
- Project matching results
- Task/ticket creation confirmation
- Error messages

## Future Enhancements

Potential improvements:
- [ ] OpenAI integration for better title generation
- [ ] Custom routing rules per project
- [ ] Email templates for auto-responses
- [ ] SLA tracking for project tasks
- [ ] Email attachment handling
- [ ] Smart assignment based on keywords

## Troubleshooting

### Email creates ticket instead of task
- **Check**: Project support_email matches exactly
- **Check**: Email server forwarding is correct
- **Check**: Database has support_email saved

### Title too generic
- **Solution**: System uses best-effort analysis
- **Manual fix**: Edit task title after creation
- **Future**: Consider OpenAI integration

### Wrong priority assigned
- **Solution**: Priority auto-detected from keywords
- **Manual fix**: Update priority in task/ticket detail
- **Customize**: Adjust keywords in `determine_priority()`

## Related Documentation
- `PROJECT_EMAIL_SUPPORT_GUIDE.md` - Initial setup guide
- `TASK_PERMISSIONS.md` - Task access control
- `SOFT_DELETE_SYSTEM.md` - Data management
