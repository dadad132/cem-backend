# Email-to-Ticket System - Quick Reference

## ✉️ How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                    EMAIL-TO-TICKET FLOW                         │
└─────────────────────────────────────────────────────────────────┘

1. CLIENT SENDS EMAIL
   ┌──────────────────────────────────────┐
   │ To: support@yourdomain.com           │
   │ Subject: Server is down - URGENT!    │
   │ Body: The main server is not         │
   │       responding. Need help ASAP!    │
   └──────────────────────────────────────┘
                    ↓

2. SYSTEM CHECKS EMAIL (Every 5 minutes)
   ┌──────────────────────────────────────┐
   │ • Connects to IMAP server            │
   │ • Fetches unread emails              │
   │ • Parses subject and body            │
   └──────────────────────────────────────┘
                    ↓

3. SMART PROCESSING
   ┌──────────────────────────────────────┐
   │ Priority Detection:                  │
   │ ✓ Found "URGENT" → Priority: URGENT  │
   │ ✓ Found "down" → Priority: URGENT    │
   │                                      │
   │ Category Detection:                  │
   │ ✓ Found "server" → Category: SUPPORT │
   │                                      │
   │ User Matching:                       │
   │ ✓ Check if email exists in workspace│
   └──────────────────────────────────────┘
                    ↓

4. TICKET CREATED
   ┌──────────────────────────────────────┐
   │ Ticket #TKT-2025-00001              │
   │ ────────────────────────────────     │
   │ Subject: Server is down - URGENT!    │
   │ Priority: 🔥 URGENT                  │
   │ Category: 🤝 Support                 │
   │ Status: Open                         │
   │ Assigned: Support Team Lead          │
   └──────────────────────────────────────┘
                    ↓

5. NOTIFICATION SENT
   ┌──────────────────────────────────────┐
   │ 🔔 Browser Notification              │
   │ "New ticket from email:              │
   │  TKT-2025-00001 - Server is down"    │
   └──────────────────────────────────────┘
```

---

## 🎯 Priority Detection

| Keywords Found | Priority Set |
|---------------|--------------|
| urgent, emergency, critical, asap, down | 🔥 **URGENT** |
| important, high priority, broken, error | ⚠️ **HIGH** |
| (no keywords) | ⚡ **MEDIUM** |

---

## 📂 Category Detection

| Keywords Found | Category Set |
|---------------|--------------|
| bug, error, broken, crash | 🐛 **BUG** |
| feature, request, enhancement | ✨ **FEATURE** |
| billing, invoice, payment | 💳 **BILLING** |
| (default) | 🤝 **SUPPORT** |

---

## ⚙️ Configuration (secrets_local.py)

```python
# Gmail Example
SUPPORT_EMAIL_IMAP_SERVER = "imap.gmail.com"
SUPPORT_EMAIL_ADDRESS = "support@yourdomain.com"
SUPPORT_EMAIL_PASSWORD = "abcdefghijklmnop"  # App Password
SUPPORT_EMAIL_DEFAULT_ASSIGNED_TO = 1  # User ID
EMAIL_CHECK_INTERVAL = 300  # 5 minutes
```

---

## 🚀 Quick Start

### 1. Get Gmail App Password
```
1. Go to: https://myaccount.google.com/apppasswords
2. Select: Mail → Other (Custom)
3. Name it: "CRM Support Tickets"
4. Copy the 16-character password
```

### 2. Configure Settings
```bash
cd app/core
cp secrets_example.py secrets_local.py
nano secrets_local.py  # Edit with your settings
```

### 3. Restart Server
```bash
python start_server.py
# Look for: "✅ Email-to-Ticket scheduler started"
```

### 4. Test It
```
1. Send email to your support address
2. Wait 5 minutes (or click "Check Emails" button)
3. Check Tickets page - new ticket should appear!
```

---

## 🎛️ Manual Check (Admin Feature)

```
┌─────────────────────────────────────┐
│  Tickets Page (Admin View)          │
│  ──────────────────────────────     │
│  [All Tickets] [Archived]           │
│  [✉️ Check Emails] [+ New Ticket]   │
│                                     │
│  Click "Check Emails" to manually   │
│  trigger email processing           │
└─────────────────────────────────────┘
```

---

## 📊 Example Conversions

### Example 1: Urgent Bug
```
📧 Email:
Subject: Payment page crashing - URGENT
Body: Users can't complete checkout. Error 500.

🎫 Created Ticket:
Priority: 🔥 URGENT (keywords: "URGENT", "crashing")
Category: 🐛 BUG (keywords: "crashing", "error")
Status: Open
```

### Example 2: Feature Request
```
📧 Email:
Subject: Add dark mode feature
Body: It would be great to have a dark theme option.

🎫 Created Ticket:
Priority: ⚡ MEDIUM (no urgent keywords)
Category: ✨ FEATURE (keywords: "feature", "add")
Status: Open
```

### Example 3: Billing Question
```
📧 Email:
Subject: Question about my invoice
Body: I was charged twice this month. Please help.

🎫 Created Ticket:
Priority: ⚡ MEDIUM
Category: 💳 BILLING (keywords: "invoice", "charged")
Status: Open
```

---

## 🔒 Security

✅ **Secure:**
- Uses App Passwords (not main password)
- secrets_local.py never committed to git
- Admin-only manual trigger
- No duplicate tickets (marks as read)

---

## 📈 Monitoring

Check console for:
```
[Email-to-Ticket] Scheduler started (checking every 300s)
[2025-11-05 18:30:00] Created 3 tickets from emails
```

---

## 🛠️ Troubleshooting

| Issue | Solution |
|-------|----------|
| "Not configured" | Add settings to secrets_local.py |
| "Failed to connect" | Check IMAP server and credentials |
| No tickets created | Verify emails are unread, check console |
| Wrong priority | Email needs keywords (urgent, important, etc.) |

---

## 📚 Full Documentation

See `EMAIL_TO_TICKET_SETUP.md` for detailed instructions.

---

**Last Updated:** November 5, 2025
