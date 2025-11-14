# Google Calendar Integration Guide

This guide explains how to set up and use Google Calendar integration in the CRM system.

## Features

- **Link Google Account**: Users can link their Google account to access Google Calendar
- **Google Calendar Access**: Read and create events in user's Google Calendar
- **Automatic Token Refresh**: Access tokens are automatically refreshed when expired

## Setup Instructions

### 1. Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the following APIs:
   - Google Calendar API
   - Google People API (for user info)

### 2. Create OAuth 2.0 Credentials

1. In Google Cloud Console, navigate to **APIs & Services > Credentials**
2. Click **Create Credentials** > **OAuth client ID**
3. Select **Web application**
4. Configure the OAuth consent screen if prompted:
   - App name: Your CRM App Name
   - User support email: Your email
   - Scopes: Add the following scopes:
     - `.../auth/userinfo.email`
     - `.../auth/userinfo.profile`
     - `.../auth/calendar`
     - `.../auth/calendar.events`
5. Add authorized redirect URIs:
   - For local development: `http://localhost:8000/web/auth/google/callback`
   - For production: `https://yourdomain.com/web/auth/google/callback`
6. Click **Create**
7. Copy the **Client ID** and **Client Secret**

### 3. Configure Environment Variables

Create a `.env` file in the project root (or update existing) with:

```bash
GOOGLE_CLIENT_ID=your_client_id_here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_client_secret_here
GOOGLE_REDIRECT_URI=http://localhost:8000/web/auth/google/callback
```

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

### 5. Run Database Migration

```bash
python migrate_google_oauth.py
```

### 6. Restart the Server

```bash
python start_server.py
```

## How to Use

### For End Users

1. **Link Google Account**:
   - Go to Profile page
   - Click "Link Google Account" button
   - Sign in with Google and authorize access
   - You'll be redirected back to the profile page

2. **Unlink Google Account**:
   - Go to Profile page
   - Click "Unlink" button next to Google Calendar Connected
   - Confirm the action

### For Developers

#### Access Google Calendar Events

```python
from app.core.google_oauth import list_calendar_events
from app.models import User
from datetime import datetime, timedelta

# Get user from database
user = await db.get(User, user_id)

if user.google_access_token:
    # Get upcoming events for the next 7 days
    events = list_calendar_events(
        access_token=user.google_access_token,
        refresh_token=user.google_refresh_token,
        token_expiry=user.google_token_expiry,
        max_results=10,
        time_min=datetime.utcnow(),
        time_max=datetime.utcnow() + timedelta(days=7)
    )
    
    if events:
        for event in events:
            print(f"Event: {event.get('summary')}")
            print(f"Start: {event.get('start').get('dateTime')}")
```

#### Create Calendar Event

```python
from app.core.google_oauth import create_calendar_event
from datetime import datetime, timedelta

# Create a meeting in user's calendar
event = create_calendar_event(
    access_token=user.google_access_token,
    refresh_token=user.google_refresh_token,
    token_expiry=user.google_token_expiry,
    summary="Team Meeting",
    description="Weekly team sync",
    start_time=datetime.utcnow() + timedelta(hours=1),
    end_time=datetime.utcnow() + timedelta(hours=2),
    attendees=["teammate@example.com"],
    location="Conference Room A"
)

if event:
    print(f"Event created: {event.get('htmlLink')}")
```

#### Refresh Token

Tokens are automatically refreshed when expired. The helper functions handle this automatically:

```python
from app.core.google_oauth import get_valid_credentials

credentials = get_valid_credentials(
    access_token=user.google_access_token,
    refresh_token=user.google_refresh_token,
    token_expiry=user.google_token_expiry
)

if credentials:
    # Token is valid or has been refreshed
    # Update user with new token if refreshed
    if credentials.token != user.google_access_token:
        user.google_access_token = credentials.token
        user.google_token_expiry = credentials.expiry
        await db.commit()
```

## Security Considerations

1. **Token Storage**: Access and refresh tokens are stored encrypted in the database
2. **HTTPS Required**: Use HTTPS in production to protect tokens in transit
3. **Token Expiry**: Access tokens expire after 1 hour, refresh tokens are long-lived
4. **Scopes**: Only request necessary scopes (calendar access)
5. **User Consent**: Users must explicitly authorize access

## Troubleshooting

### Error: "Google integration is not configured"

- Check that `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set in `.env`
- Verify the values are correct from Google Cloud Console

### Error: "Redirect URI mismatch"

- Ensure the redirect URI in `.env` matches exactly what's configured in Google Cloud Console
- Check for trailing slashes (should not have one)
- Verify http vs https

### Error: "Invalid grant" or "Token expired"

- The refresh token may have expired or been revoked
- User needs to unlink and relink their Google account

### Calendar events not showing

- Check that user has linked their Google account (`user.google_id` is not null)
- Verify token hasn't expired
- Check that Google Calendar API is enabled in Google Cloud Console

## API Endpoints

- `GET /web/auth/google/link` - Initiate OAuth flow
- `GET /web/auth/google/callback` - OAuth callback handler
- `POST /web/auth/google/unlink` - Unlink Google account

## Database Schema

New fields added to `user` table:

- `google_id` (TEXT) - Google user ID
- `google_access_token` (TEXT) - OAuth access token
- `google_refresh_token` (TEXT) - OAuth refresh token  
- `google_token_expiry` (TIMESTAMP) - Token expiration time

## Future Enhancements

- [ ] Sync CRM meetings to Google Calendar automatically
- [ ] Import Google Calendar events to CRM
- [ ] Two-way sync with conflict resolution
- [ ] Support for multiple calendars
- [ ] Calendar event reminders
- [ ] Meeting room availability checking
