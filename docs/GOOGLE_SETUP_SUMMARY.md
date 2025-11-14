# Google Calendar Integration - Quick Setup

## ✅ What's Been Done

### 1. Database Changes
- Added 4 new fields to the `user` table:
  - `google_id` - Stores the Google user ID
  - `google_access_token` - OAuth access token
  - `google_refresh_token` - OAuth refresh token
  - `google_token_expiry` - Token expiration timestamp
- Created index on `google_id` for faster lookups
- Migration completed successfully

### 2. Backend Features Added
- **OAuth Routes**:
  - `/web/auth/google/link` - Starts Google OAuth flow
  - `/web/auth/google/callback` - Handles OAuth callback
  - `/web/auth/google/unlink` - Unlinks Google account

- **Google Calendar Helper** (`app/core/google_oauth.py`):
  - `get_authorization_url()` - Generate OAuth URL
  - `exchange_code_for_tokens()` - Get tokens from auth code
  - `get_google_user_info()` - Fetch user profile
  - `refresh_access_token()` - Refresh expired tokens
  - `list_calendar_events()` - Fetch calendar events
  - `create_calendar_event()` - Create new events
  - `get_calendar_service()` - Get Calendar API service

### 3. UI Updates
- Profile page now shows:
  - "Link Google Account" button (when not linked)
  - "Google Calendar Connected" status with Unlink button (when linked)
  - Success/error messages for OAuth flow
  - Beautiful Google logo and styling

### 4. Dependencies Installed
- `google-auth==2.36.0`
- `google-auth-oauthlib==1.2.1`
- `google-auth-httplib2==0.2.0`
- `google-api-python-client==2.153.0`

## 🚀 Next Steps - IMPORTANT!

### Configure Google Cloud Console

**You MUST do this before the feature will work:**

1. **Go to Google Cloud Console**: https://console.cloud.google.com/

2. **Create/Select Project**:
   - Create a new project or use existing one
   - Name it something like "CRM Calendar Integration"

3. **Enable APIs**:
   - Go to "APIs & Services" > "Library"
   - Search and enable:
     - **Google Calendar API**
     - **Google People API**

4. **Configure OAuth Consent Screen**:
   - Go to "APIs & Services" > "OAuth consent screen"
   - Select "External" user type
   - Fill in:
     - App name: "Your CRM App"
     - User support email: your email
     - Developer contact: your email
   - Add scopes:
     - `../auth/userinfo.email`
     - `../auth/userinfo.profile`
     - `../auth/calendar`
     - `../auth/calendar.events`
   - Add test users (your email) if in testing mode

5. **Create OAuth Credentials**:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Application type: **Web application**
   - Name: "CRM Web Client"
   - Authorized redirect URIs - add:
     - `http://localhost:8000/web/auth/google/callback`
     - `http://192.168.18.34:8000/web/auth/google/callback` (your local IP)
     - For production: `https://yourdomain.com/web/auth/google/callback`
   - Click "Create"
   - **COPY the Client ID and Client Secret**

6. **Update .env File**:
   Create or update `.env` file in project root:
   ```bash
   GOOGLE_CLIENT_ID=your_client_id_here.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your_client_secret_here
   GOOGLE_REDIRECT_URI=http://localhost:8000/web/auth/google/callback
   ```

7. **Restart Server**:
   ```bash
   # Stop current server (Ctrl+C)
   python start_server.py
   ```

## 📖 How to Use

### For Users:
1. Go to **Profile page** (click your name in navigation)
2. Scroll to "Google Integration" section
3. Click "**Link Google Account**"
4. Sign in with Google
5. Grant calendar permissions
6. You'll be redirected back - account is now linked!

### For Developers:

**List upcoming calendar events:**
```python
from app.core.google_oauth import list_calendar_events

events = list_calendar_events(
    access_token=user.google_access_token,
    refresh_token=user.google_refresh_token,
    token_expiry=user.google_token_expiry,
    max_results=10
)

for event in events:
    print(f"{event['summary']} - {event['start']['dateTime']}")
```

**Create a calendar event:**
```python
from app.core.google_oauth import create_calendar_event
from datetime import datetime, timedelta

event = create_calendar_event(
    access_token=user.google_access_token,
    refresh_token=user.google_refresh_token,
    token_expiry=user.google_token_expiry,
    summary="Team Meeting",
    description="Weekly sync",
    start_time=datetime.utcnow() + timedelta(hours=1),
    end_time=datetime.utcnow() + timedelta(hours=2),
    attendees=["teammate@example.com"]
)
```

## 📁 Files Created/Modified

**New Files:**
- `migrate_google_oauth.py` - Database migration script
- `app/core/google_oauth.py` - Google OAuth and Calendar helper
- `GOOGLE_CALENDAR_INTEGRATION.md` - Detailed documentation
- `.env.example` - Updated with Google config

**Modified Files:**
- `app/models/user.py` - Added Google OAuth fields
- `app/web/routes.py` - Added OAuth routes
- `app/templates/auth/profile.html` - Added Link Google button
- `requirements.txt` - Added Google libraries

## ⚠️ Important Notes

1. **Without Google Cloud Setup**: The "Link Google Account" button will show an error. You MUST configure Google Cloud Console first.

2. **Token Security**: Access tokens expire after 1 hour, but they're automatically refreshed using the refresh token.

3. **HTTPS in Production**: For production deployment, use HTTPS and update the redirect URI accordingly.

4. **Privacy**: Tokens are stored in the database. In production, consider encrypting them.

## 🔧 Troubleshooting

**"Google integration is not configured"**
→ Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`

**"Redirect URI mismatch"**
→ Ensure redirect URI in `.env` matches Google Cloud Console exactly

**Calendar events not showing**
→ Check that user has linked account and Google Calendar API is enabled

## 📚 Documentation

See `GOOGLE_CALENDAR_INTEGRATION.md` for comprehensive documentation including:
- Detailed setup steps
- Code examples
- Security considerations
- Troubleshooting guide
- API reference
