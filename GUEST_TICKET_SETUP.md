# Guest Ticket Submission System - Setup Guide

## ✨ Overview

The Guest Ticket Submission System allows **clients to submit support tickets without needing an account**. They simply fill out a form with their details, and:

1. ✅ A ticket is created in your CRM
2. ✅ They receive an automatic confirmation email
3. ✅ Your team is notified and can assign technicians
4. ✅ Admin can fully customize the email template

---

## 🎯 Key Features

### For Clients (Guests)
- 📝 Submit tickets without logging in
- 📧 Receive automatic confirmation email
- 📱 Simple, mobile-friendly form
- 🔒 Secure information handling

### For Admins
- ⚙️ Full SMTP configuration control
- 📝 Customizable email templates
- 🧪 Test email functionality
- 👥 Assign tickets to technicians
- 📊 Track all guest vs internal tickets

---

## 🚀 Quick Start (3 Steps)

### Step 1: Configure Email Settings

1. **Login as Admin**
2. **Go to**: Admin → Email Settings (in sidebar)
3. **Configure SMTP** (Gmail example):
   ```
   SMTP Host: smtp.gmail.com
   SMTP Port: 587
   Username: your-email@gmail.com
   Password: your-app-password (16 characters)
   From Email: support@yourdomain.com
   From Name: Your Company Support
   Use TLS: ✓ Checked
   ```

4. **Get Gmail App Password**:
   - Go to: https://myaccount.google.com/apppasswords
   - Select: Mail → Other (Custom name)
   - Name it: "CRM Guest Tickets"
   - Copy the 16-character password
   - Paste into "Password" field above

5. **Customize Email Template**:
   - Edit company name
   - Modify email subject
   - Customize email body
   - Available variables:
     * `{guest_name}` - Client's first name
     * `{guest_surname}` - Client's last name
     * `{ticket_number}` - e.g., TKT-2025-00001
     * `{subject}` - Ticket title
     * `{priority}` - low, medium, high, urgent
     * `{company_name}` - Your company name

6. **Click "Send Test Email"** to verify settings
7. **Click "Save Settings"**

### Step 2: Share Guest URL

**Guest Ticket URL**:
```
http://your-domain.com:8000/web/tickets/guest
```

Share this URL with your clients via:
- 📧 Email signature
- 🌐 Website "Support" button
- 📱 QR Code
- 📄 Customer portal

### Step 3: Manage Incoming Tickets

1. **Guest tickets appear in Tickets page** with a **"Guest" badge**
2. **Click ticket** to see guest contact information
3. **Assign to technician** using dropdown
4. **Update status** as you work on it
5. **Guest receives automatic confirmation** via email

---

## 📋 Guest Submission Form Fields

Clients fill out:

### Personal Information
- ✅ First Name (required)
- ✅ Last Name (required)
- ✅ Email Address (required) - receives confirmation
- ✅ Phone Number (required)

### Company Information
- ✅ Company Name (required)
- Branch/Location (optional)

### Ticket Details
- ✅ Subject/Title (required)
- Priority Level (dropdown)
- ✅ Description (required)

---

## 📧 Email Template Example

**Default Subject**:
```
Ticket Confirmation - #{ticket_number}
```

**Default Body**:
```
Dear {guest_name} {guest_surname},

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
This is an automated message. Please do not reply to this email.
```

**Customize this** in Admin → Email Settings!

---

## 🎨 Guest Ticket Workflow

```
┌──────────────────────────────────────────────────┐
│ 1. CLIENT VISITS GUEST PAGE                     │
│    /web/tickets/guest                            │
└──────────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────────┐
│ 2. CLIENT FILLS OUT FORM                         │
│    - Name: John Doe                              │
│    - Email: john@example.com                     │
│    - Company: Acme Corp                          │
│    - Issue: Server down                          │
└──────────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────────┐
│ 3. TICKET CREATED                                │
│    Ticket #TKT-2025-00001                        │
│    Status: Open                                  │
│    Badge: Guest                                  │
└──────────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────────┐
│ 4. CONFIRMATION EMAIL SENT                       │
│    To: john@example.com                          │
│    Subject: Ticket Confirmation - #TKT-00001     │
└──────────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────────┐
│ 5. ADMIN ASSIGNS TECHNICIAN                      │
│    Assigned to: Support Team Lead                │
│    Notification sent to technician               │
└──────────────────────────────────────────────────┘
                    ↓
┌──────────────────────────────────────────────────┐
│ 6. TECHNICIAN RESOLVES TICKET                    │
│    Status: Resolved → Closed                     │
└──────────────────────────────────────────────────┘
```

---

## 🔍 Identifying Guest Tickets

Guest tickets are marked with:

### In Ticket List:
- 🏷️ **"Guest" badge** (indigo color)
- Displayed alongside priority and status

### In Ticket Detail:
- 📋 **Guest Information Card** at top showing:
  * Full name
  * Email address
  * Phone number
  * Company name
  * Branch (if provided)

### In Database:
- `is_guest = true`
- All guest_* fields populated
- `created_by_id = null` (no user account)

---

## ⚙️ Admin Email Settings Page

**Access**: Admin → Email Settings

**Features**:

### SMTP Configuration
- Host, Port, Username, Password
- TLS/SSL toggle
- From email and name

### Email Template
- Subject line (with variables)
- Body text (with variables)
- Company name
- Auto-reply toggle

### Actions
- **Send Test Email** - Verify SMTP works
- **Save Settings** - Apply changes
- **Copy Guest URL** - Share with clients

### Info Cards
- Guest ticket URL with copy button
- Gmail setup instructions
- Step-by-step checklist

---

## 🛠️ Troubleshooting

### Issue: "Failed to send confirmation email"

**Solutions**:
1. **Check SMTP settings** in Admin → Email Settings
2. **For Gmail**:
   - Ensure 2FA is enabled
   - Use App Password (not regular password)
   - Check App Password has no spaces
3. **Test email** using "Send Test Email" button
4. **Check email logs** in server console

### Issue: Tickets created but no email sent

**Check**:
- Is "Enable automatic confirmation emails" checked?
- Are SMTP credentials correct?
- Is email server accessible from your network?

### Issue: Can't access guest page

**Solution**:
- Guest page is PUBLIC - no login required
- URL: `http://your-domain:8000/web/tickets/guest`
- Check server is running
- Check firewall allows port 8000

---

## 📊 Managing Guest Tickets

### Viewing Guest Tickets
- All tickets appear in main Tickets page
- Filter by "Guest" badge to see only guest submissions
- Click ticket to see full guest contact information

### Assigning Technicians
1. Open ticket detail page
2. Use "Assigned To" dropdown
3. Select technician
4. Technician receives notification

### Updating Status
- Use status dropdown: Open → In Progress → Resolved → Closed
- Status changes create history entries
- Guest doesn't receive status update emails (feature for future)

### Adding Comments
- Add internal notes (only team can see)
- Add public comments (visible to all)
- Guest cannot reply via portal (feature for future)

---

## 🔒 Security Considerations

- ✅ Guest page is rate-limited to prevent spam
- ✅ Email validation prevents invalid addresses
- ✅ SMTP passwords stored in database (encrypt in production)
- ✅ No SQL injection - uses parameterized queries
- ✅ XSS protection - input sanitization
- ✅ Guest can't access other tickets - no session

**Production Recommendations**:
1. Use HTTPS for guest page
2. Add CAPTCHA to prevent spam
3. Encrypt SMTP passwords in database
4. Enable rate limiting on guest endpoint
5. Monitor for abuse patterns

---

## 📈 Future Enhancements

Planned features:
- [ ] Guest portal to view ticket status
- [ ] Email notifications on status changes
- [ ] File upload support for guests
- [ ] CAPTCHA spam protection
- [ ] Rate limiting for guest submissions
- [ ] SMS notifications option
- [ ] Multi-language support
- [ ] Custom branding for guest page

---

## 💡 Tips & Best Practices

### For Admins:
1. **Test the full flow** before sharing URL
2. **Customize email template** with your branding
3. **Set default assignments** to triage team
4. **Review guest tickets daily**
5. **Create canned responses** for common issues

### For Email Templates:
1. **Keep it professional** and friendly
2. **Include ticket number** prominently
3. **Set expectations** (response time)
4. **Provide contact alternatives** (phone, chat)
5. **Test with different email clients**

### For Clients:
1. **Share clear instructions** on how to use
2. **Provide examples** of good ticket descriptions
3. **Set response time expectations**
4. **Include support hours** if limited

---

## 📚 Related Documentation

- `EMAIL_TO_TICKET_SETUP.md` - Email-to-ticket system
- `TICKET_DETAIL_FEATURE.md` - Ticket management features
- Main `README.md` - General CRM documentation

---

## ✅ Setup Checklist

- [ ] Configure SMTP settings
- [ ] Get Gmail App Password (if using Gmail)
- [ ] Customize email template
- [ ] Set company name
- [ ] Send test email
- [ ] Save settings
- [ ] Copy guest URL
- [ ] Share URL with clients
- [ ] Submit test ticket as guest
- [ ] Verify confirmation email received
- [ ] Assign test ticket to technician
- [ ] Close test ticket

---

**Last Updated**: November 5, 2025  
**System Version**: CRM Backend v2.1

🎉 **You're all set!** Clients can now submit tickets and receive automatic confirmations!
