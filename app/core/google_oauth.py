"""
Google OAuth and Calendar integration
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

from .config import get_settings

# Get settings
_settings = get_settings()

# Google OAuth configuration
GOOGLE_CLIENT_ID = _settings.google_client_id
GOOGLE_CLIENT_SECRET = _settings.google_client_secret
GOOGLE_REDIRECT_URI = _settings.google_redirect_uri

# OAuth 2.0 scopes
SCOPES = [
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.events'
]


def get_google_oauth_flow(state: Optional[str] = None) -> Flow:
    """
    Create a Google OAuth flow object
    """
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI
    )
    
    if state:
        flow.state = state
    
    return flow


def get_authorization_url(user_id: int) -> tuple[str, str]:
    """
    Generate Google OAuth authorization URL
    Returns: (authorization_url, state)
    """
    flow = get_google_oauth_flow()
    # Use user_id as state for security
    flow.state = str(user_id)
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'  # Force consent to get refresh token
    )
    return authorization_url, state


def exchange_code_for_tokens(code: str, state: str) -> Dict[str, Any]:
    """
    Exchange authorization code for access and refresh tokens
    Returns: dict with token information
    """
    flow = get_google_oauth_flow(state)
    flow.fetch_token(code=code)
    
    credentials = flow.credentials
    
    return {
        'access_token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_expiry': credentials.expiry,
        'scopes': credentials.scopes
    }


def get_google_user_info(access_token: str) -> Dict[str, Any]:
    """
    Get user info from Google using access token
    """
    credentials = Credentials(token=access_token)
    service = build('oauth2', 'v2', credentials=credentials)
    user_info = service.userinfo().get().execute()
    return user_info


def refresh_access_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    """
    Refresh the access token using refresh token
    Returns: dict with new access token and expiry, or None if failed
    """
    try:
        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            scopes=SCOPES
        )
        
        credentials.refresh(Request())
        
        return {
            'access_token': credentials.token,
            'token_expiry': credentials.expiry
        }
    except Exception as e:
        print(f"Error refreshing token: {e}")
        return None


def get_valid_credentials(access_token: str, refresh_token: str, token_expiry: datetime) -> Optional[Credentials]:
    """
    Get valid credentials, refreshing if necessary
    """
    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES
    )
    
    # Check if token is expired or will expire soon (within 5 minutes)
    if token_expiry and datetime.utcnow() >= token_expiry - timedelta(minutes=5):
        try:
            credentials.refresh(Request())
        except Exception as e:
            print(f"Error refreshing credentials: {e}")
            return None
    
    return credentials


def get_calendar_service(access_token: str, refresh_token: str, token_expiry: datetime):
    """
    Get Google Calendar service object
    """
    credentials = get_valid_credentials(access_token, refresh_token, token_expiry)
    if not credentials:
        return None
    
    return build('calendar', 'v3', credentials=credentials)


def list_calendar_events(
    access_token: str, 
    refresh_token: str, 
    token_expiry: datetime,
    max_results: int = 10,
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None
) -> Optional[list]:
    """
    List calendar events from user's Google Calendar
    """
    try:
        service = get_calendar_service(access_token, refresh_token, token_expiry)
        if not service:
            return None
        
        # Default to upcoming events if no time range specified
        if not time_min:
            time_min = datetime.utcnow()
        
        time_min_str = time_min.isoformat() + 'Z'
        time_max_str = time_max.isoformat() + 'Z' if time_max else None
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min_str,
            timeMax=time_max_str,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        return events_result.get('items', [])
    
    except Exception as e:
        print(f"Error fetching calendar events: {e}")
        return None


def create_calendar_event(
    access_token: str,
    refresh_token: str,
    token_expiry: datetime,
    summary: str,
    description: str,
    start_time: datetime,
    end_time: datetime,
    attendees: Optional[list] = None,
    location: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Create an event in user's Google Calendar
    """
    try:
        service = get_calendar_service(access_token, refresh_token, token_expiry)
        if not service:
            return None
        
        event = {
            'summary': summary,
            'description': description,
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'UTC',
            },
        }
        
        if location:
            event['location'] = location
        
        if attendees:
            event['attendees'] = [{'email': email} for email in attendees]
        
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        return created_event
    
    except Exception as e:
        print(f"Error creating calendar event: {e}")
        return None
