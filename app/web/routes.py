from __future__ import annotations

from typing import Optional
from pathlib import Path
from datetime import date, datetime, timedelta, time, timezone
import calendar as pycalendar
import os
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, File, UploadFile, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import verify_password, get_password_hash
from app.core.email import send_email
from app.core.email_to_ticket_v2 import get_local_time
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.models.enums import TaskStatus, TaskPriority, MeetingPlatform
from app.models.workspace import Workspace
from app.models.assignment import Assignment
from app.models.comment import Comment
from app.models.comment_attachment import CommentAttachment
from app.models.task_history import TaskHistory
from app.models.notification import Notification
from app.models.chat import Chat, ChatMember, Message
from app.models.meeting import Meeting, MeetingAttendee
from app.models.company import Company
from app.models.contact import Contact
from app.models.lead import Lead, LeadStatus, LeadSource
from app.models.deal import Deal, DealStage
from app.models.activity import Activity, ActivityType

BASE_DIR = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=str(BASE_DIR / 'templates'))

# Convert UTC datetime to local time for display
def utc_to_local(utc_dt):
    """Convert UTC datetime to local time"""
    if utc_dt is None:
        return None
    # Get the local timezone offset
    import time
    if time.daylight:
        offset_seconds = time.altzone
    else:
        offset_seconds = time.timezone
    # Calculate local time
    from datetime import timedelta
    local_dt = utc_dt - timedelta(seconds=offset_seconds)
    return local_dt

# Add helper functions to Jinja2 globals for use in templates
templates.env.globals['now'] = datetime.utcnow
templates.env.globals['utc_to_local'] = utc_to_local

router = APIRouter(tags=['web'])

# --------------------------
# Auth (session-based for web)
# --------------------------
@router.get('/login', response_class=HTMLResponse)
async def web_login(request: Request):
    return templates.TemplateResponse('auth/login.html', {'request': request, 'error': None})


@router.post('/login')
async def web_login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse('auth/login.html', {'request': request, 'error': 'Invalid username or password'}, status_code=400)
    
    # Check if user is active
    if not user.is_active:
        return templates.TemplateResponse('auth/login.html', {'request': request, 'error': 'Your account has been deactivated. Please contact your administrator.'}, status_code=403)
    
    request.session['user_id'] = user.id
    # Redirect to profile completion if not completed
    if not user.profile_completed:
        return RedirectResponse('/web/profile/complete', status_code=303)
    return RedirectResponse('/web/projects', status_code=303)


@router.get('/signup', response_class=HTMLResponse)
async def web_signup(request: Request):
    return templates.TemplateResponse('auth/signup.html', {'request': request, 'error': None})


@router.post('/signup')
async def web_signup_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    # Import validation function
    from app.core.security import validate_password
    
    # Validate password
    is_valid, error_msg = validate_password(password)
    if not is_valid:
        return templates.TemplateResponse('auth/signup.html', {'request': request, 'error': error_msg}, status_code=400)
    
    exists = await db.execute(select(User).where(User.username == username))
    if exists.scalar_one_or_none():
        return templates.TemplateResponse('auth/signup.html', {'request': request, 'error': 'Username already taken'}, status_code=400)
    # Create workspace and user
    # Self-registered users become admin of their own workspace
    ws = Workspace(name=f"{username}'s Workspace")
    db.add(ws)
    await db.flush()
    user = User(
        username=username, 
        hashed_password=get_password_hash(password), 
        workspace_id=ws.id,
        profile_completed=False,
        email_verified=True,
        is_admin=True  # Self-registered users are admins of their workspace
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    request.session['user_id'] = user.id
    return RedirectResponse('/web/profile/complete', status_code=303)


@router.post('/logout')
async def web_logout(request: Request):
    request.session.clear()
    return RedirectResponse('/', status_code=303)


# --------------------------
# Profile Completion
# --------------------------
@router.get('/profile/complete', response_class=HTMLResponse)
async def web_profile_complete(request: Request, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    if user.profile_completed:
        return RedirectResponse('/web/projects', status_code=303)
    return templates.TemplateResponse('auth/profile_complete.html', {'request': request, 'user': user, 'error': None})


@router.post('/profile/complete')
async def web_profile_complete_post(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    preferred_meeting_platform: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session),
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    # Check if email is already used by another user
    if email:
        exists = await db.execute(select(User).where(User.email == email, User.id != user_id))
        if exists.scalar_one_or_none():
            return templates.TemplateResponse('auth/profile_complete.html', {'request': request, 'user': user, 'error': 'Email already in use'}, status_code=400)
    user.full_name = full_name
    user.email = email
    if preferred_meeting_platform:
        try:
            user.preferred_meeting_platform = MeetingPlatform(preferred_meeting_platform)
        except ValueError:
            pass
    user.profile_completed = True
    await db.commit()
    return RedirectResponse('/web/projects', status_code=303)


@router.get('/profile', response_class=HTMLResponse)
async def web_profile(request: Request, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    return templates.TemplateResponse('auth/profile.html', {'request': request, 'user': user})


@router.post('/profile')
async def web_profile_post(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    preferred_meeting_platform: Optional[str] = Form(None),
    calendar_color: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session),
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Check if email is already used by another user
    if email:
        exists = await db.execute(select(User).where(User.email == email, User.id != user_id))
        if exists.scalar_one_or_none():
            return templates.TemplateResponse('auth/profile.html', {
                'request': request, 
                'user': user, 
                'error': 'Email already in use'
            }, status_code=400)
    
    user.full_name = full_name
    user.email = email
    if preferred_meeting_platform:
        try:
            user.preferred_meeting_platform = MeetingPlatform(preferred_meeting_platform)
        except ValueError:
            pass
    if calendar_color:
        user.calendar_color = calendar_color
    
    await db.commit()
    return templates.TemplateResponse('auth/profile.html', {
        'request': request, 
        'user': user, 
        'success': 'Profile updated successfully'
    })


@router.post('/profile/picture')
async def web_profile_picture_upload(
    request: Request,
    profile_picture: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Validate file type
    allowed_types = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
    if profile_picture.content_type not in allowed_types:
        return templates.TemplateResponse('auth/profile.html', {
            'request': request,
            'user': user,
            'error': 'Invalid file type. Please upload a JPEG, PNG, GIF, or WebP image.'
        }, status_code=400)
    
    # Validate file size (max 5MB)
    content = await profile_picture.read()
    if len(content) > 5 * 1024 * 1024:
        return templates.TemplateResponse('auth/profile.html', {
            'request': request,
            'user': user,
            'error': 'File too large. Maximum size is 5MB.'
        }, status_code=400)
    
    # Create uploads directory if it doesn't exist
    upload_dir = BASE_DIR / 'uploads' / 'profile_pictures'
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Delete old profile picture if it exists
    if user.profile_picture:
        old_file = BASE_DIR / user.profile_picture.lstrip('/')
        if old_file.exists():
            old_file.unlink()
    
    # Generate unique filename
    file_extension = profile_picture.filename.split('.')[-1] if '.' in profile_picture.filename else 'jpg'
    filename = f"{user_id}_{uuid.uuid4().hex[:8]}.{file_extension}"
    file_path = upload_dir / filename
    
    # Save file
    with open(file_path, 'wb') as f:
        f.write(content)
    
    # Update user profile picture path (relative to BASE_DIR)
    user.profile_picture = f"/uploads/profile_pictures/{filename}"
    await db.commit()
    
    return RedirectResponse('/web/profile?success=picture', status_code=303)


@router.get('/uploads/profile_pictures/{filename}')
async def serve_profile_picture(filename: str):
    """Serve profile picture files"""
    # Prevent path traversal attacks
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_path = BASE_DIR / 'uploads' / 'profile_pictures' / filename
    
    # Ensure the resolved path is within the uploads directory
    try:
        file_path = file_path.resolve()
        upload_base = (BASE_DIR / 'uploads' / 'profile_pictures').resolve()
        if not str(file_path).startswith(str(upload_base)):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file path")
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Profile picture not found")
    return FileResponse(file_path)


# --------------------------
# Google OAuth Integration
# --------------------------
@router.get('/auth/google/link')
async def web_google_oauth_link(request: Request, db: AsyncSession = Depends(get_session)):
    """Initiate Google OAuth flow to link account"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    try:
        from app.core.config import get_settings
        from app.core.google_oauth import get_authorization_url
        
        settings = get_settings()
        
        # Check if Google OAuth is configured
        if not settings.google_client_id or not settings.google_client_secret:
            print(f"[!] Google OAuth not configured. Client ID: {settings.google_client_id[:20] if settings.google_client_id else 'EMPTY'}, Secret: {'SET' if settings.google_client_secret else 'EMPTY'}")
            return RedirectResponse('/web/profile?error=google_config', status_code=303)
        
        print(f"[*] Starting Google OAuth for user {user_id}")
        print(f"[*] Client ID: {settings.google_client_id[:50]}...")
        print(f"[*] Redirect URI: {settings.google_redirect_uri}")
        
        auth_url, state = get_authorization_url(user_id)
        # Store state in session for verification
        request.session['google_oauth_state'] = state
        print(f"[*] Redirecting to Google OAuth URL")
        return RedirectResponse(auth_url, status_code=303)
    except Exception as e:
        print(f"[!] Error initiating Google OAuth: {e}")
        import traceback
        traceback.print_exc()
        return RedirectResponse('/web/profile?error=google_config', status_code=303)


@router.get('/auth/google/callback')
async def web_google_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(get_session)
):
    """Handle Google OAuth callback"""
    if error:
        return RedirectResponse(f'/web/profile?error=google_denied', status_code=303)
    
    if not code or not state:
        return RedirectResponse(f'/web/profile?error=google_invalid', status_code=303)
    
    # Verify state matches session
    session_state = request.session.get('google_oauth_state')
    if not session_state or session_state != state:
        return RedirectResponse(f'/web/profile?error=google_state_mismatch', status_code=303)
    
    try:
        user_id = int(state)
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        
        if not user or not user.is_active:
            return RedirectResponse('/web/login', status_code=303)
        
        from app.core.google_oauth import exchange_code_for_tokens, get_google_user_info
        
        # Exchange code for tokens
        token_info = exchange_code_for_tokens(code, state)
        
        # Get user info from Google
        google_user_info = get_google_user_info(token_info['access_token'])
        
        # Update user with Google credentials
        user.google_id = google_user_info.get('id')
        user.google_access_token = token_info['access_token']
        user.google_refresh_token = token_info['refresh_token']
        user.google_token_expiry = token_info['token_expiry']
        
        await db.commit()
        
        # Clear OAuth state from session
        request.session.pop('google_oauth_state', None)
        
        return RedirectResponse('/web/profile?success=google_linked', status_code=303)
        
    except Exception as e:
        print(f"Error in Google OAuth callback: {e}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(f'/web/profile?error=google_failed', status_code=303)


@router.post('/auth/google/unlink')
async def web_google_oauth_unlink(request: Request, db: AsyncSession = Depends(get_session)):
    """Unlink Google account"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Clear Google OAuth fields
    user.google_id = None
    user.google_access_token = None
    user.google_refresh_token = None
    user.google_token_expiry = None
    
    await db.commit()
    
    return RedirectResponse('/web/profile?success=google_unlinked', status_code=303)


# --------------------------
# Email Verification (kept for later, not enforced)
# --------------------------
@router.get('/verify-email', response_class=HTMLResponse)
async def web_verify_email(request: Request, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    return templates.TemplateResponse('auth/verify_email.html', {'request': request, 'user': user, 'error': None, 'sent': False})


@router.post('/verify-email/request')
async def web_verify_email_request(request: Request, db: AsyncSession = Depends(get_session)):
    # No-op while OTP disabled
    return templates.TemplateResponse('auth/verify_email.html', {'request': request, 'user': None, 'error': None, 'sent': True})


@router.post('/verify-email/confirm')
async def web_verify_email_confirm(request: Request, code: str = Form(...), db: AsyncSession = Depends(get_session)):
    # No-op while OTP disabled
    return RedirectResponse('/web/projects', status_code=303)


# --------------------------
# Notifications (minimal for layout)
# --------------------------
@router.get('/notifications/count', response_class=HTMLResponse)
async def web_notifications_count(request: Request, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return HTMLResponse('0')
    result = await db.execute(select(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None)))
    count = len(result.scalars().all())
    return HTMLResponse(str(count))


@router.get('/notifications/peek', response_class=HTMLResponse)
async def web_notifications_peek(request: Request, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return HTMLResponse('')
    # Show nothing while OTP is disabled (simplify)
    return HTMLResponse('')


@router.get('/notifications', response_class=HTMLResponse)
async def web_notifications(request: Request, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Get all notifications for the user, ordered by newest first
    notifications = (await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    )).scalars().all()
    
    return templates.TemplateResponse('notifications/list.html', {
        'request': request,
        'user': user,
        'notifications': notifications
    })


@router.post('/notifications/{notification_id}/read')
async def web_notification_mark_read(
    request: Request,
    notification_id: int,
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    notification = (await db.execute(
        select(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
    )).scalar_one_or_none()
    
    if notification:
        from datetime import datetime
        notification.read_at = datetime.utcnow()
        await db.commit()
        
        # Smart navigation based on notification type
        if notification.url:
            return RedirectResponse(notification.url, status_code=303)
        elif notification.type == 'meeting' and notification.related_id:
            return RedirectResponse(f'/web/meetings?highlight={notification.related_id}', status_code=303)
        elif notification.type in ['task', 'assignment'] and notification.related_id:
            return RedirectResponse(f'/web/tasks/my?highlight={notification.related_id}', status_code=303)
        elif notification.type == 'message':
            # For messages, navigate to the chat
            return RedirectResponse(notification.url or '/web/chats', status_code=303)
    
    return RedirectResponse('/web/notifications', status_code=303)


@router.post('/notifications/{notification_id}/delete')
async def web_notification_delete(
    request: Request,
    notification_id: int,
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    notification = (await db.execute(
        select(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
    )).scalar_one_or_none()
    
    if notification:
        await db.delete(notification)
        await db.commit()
    
    return RedirectResponse('/web/notifications', status_code=303)


@router.post('/notifications/{notification_id}/dismiss')
async def web_notification_dismiss(
    request: Request,
    notification_id: int,
    db: AsyncSession = Depends(get_session)
):
    """Mark notification popup as dismissed (auto-dismiss after 1 minute)"""
    user_id = request.session.get('user_id')
    if not user_id:
        return HTMLResponse('', status_code=401)
    
    notification = (await db.execute(
        select(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
    )).scalar_one_or_none()
    
    if notification:
        from datetime import datetime
        notification.dismissed_at = datetime.utcnow()
        await db.commit()
    
    return HTMLResponse('OK')


@router.get('/notifications/unread', response_class=HTMLResponse)
async def web_notifications_unread(request: Request, db: AsyncSession = Depends(get_session)):
    """Get unread and undismissed notifications for popup display"""
    user_id = request.session.get('user_id')
    if not user_id:
        return HTMLResponse('[]')
    
    notifications = (await db.execute(
        select(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
            Notification.dismissed_at.is_(None)
        )
        .order_by(Notification.created_at.desc())
        .limit(5)
    )).scalars().all()
    
    import json
    notification_data = [{
        'id': n.id,
        'type': n.type,
        'message': n.message,
        'url': n.url,
        'related_id': n.related_id,
        'created_at': n.created_at.isoformat() if n.created_at else None
    } for n in notifications]
    
    return HTMLResponse(json.dumps(notification_data), media_type='application/json')


@router.post('/notifications/mark-all-read')
async def web_notifications_mark_all_read(
    request: Request,
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    from datetime import datetime
    await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
    )
    
    # Update all unread notifications
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
    )
    notifications = result.scalars().all()
    
    for notification in notifications:
        notification.read_at = datetime.utcnow()
    
    await db.commit()
    return RedirectResponse('/web/notifications', status_code=303)


@router.get('/search', response_class=HTMLResponse)
async def web_search(
    request: Request,
    q: str = Query(""),
    db: AsyncSession = Depends(get_session)
):
    """Search across tasks and projects"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    results = {'tasks': [], 'projects': [], 'comments': []}
    
    if q and len(q.strip()) >= 2:
        search_term = f"%{q.strip()}%"
        
        # Search tasks
        task_query = (
            select(Task)
            .join(Project, Task.project_id == Project.id)
            .where(Project.workspace_id == user.workspace_id)
            .where(
                (Task.title.ilike(search_term)) | 
                (Task.description.ilike(search_term))
            )
            .limit(20)
        )
        tasks = (await db.execute(task_query)).scalars().all()
        
        for task in tasks:
            project = (await db.execute(select(Project).where(Project.id == task.project_id))).scalar_one_or_none()
            results['tasks'].append({
                'id': task.id,
                'title': task.title,
                'description': task.description or '',
                'status': task.status.value if task.status else 'todo',
                'priority': task.priority.value if task.priority else 'medium',
                'project_name': project.name if project else 'Unknown',
                'project_id': project.id if project else None,
            })
        
        # Search projects
        project_query = (
            select(Project)
            .where(Project.workspace_id == user.workspace_id)
            .where(
                (Project.name.ilike(search_term)) | 
                (Project.description.ilike(search_term))
            )
            .limit(20)
        )
        projects = (await db.execute(project_query)).scalars().all()
        
        for project in projects:
            task_count = (await db.execute(
                select(Task).where(Task.project_id == project.id)
            )).scalars().all()
            results['projects'].append({
                'id': project.id,
                'name': project.name,
                'description': project.description or '',
                'task_count': len(task_count),
            })
        
        # Search comments
        comment_query = (
            select(Comment)
            .join(Task, Comment.task_id == Task.id)
            .join(Project, Task.project_id == Project.id)
            .where(Project.workspace_id == user.workspace_id)
            .where(Comment.content.ilike(search_term))
            .limit(20)
        )
        comments = (await db.execute(comment_query)).scalars().all()
        
        for comment in comments:
            task = (await db.execute(select(Task).where(Task.id == comment.task_id))).scalar_one_or_none()
            author = (await db.execute(select(User).where(User.id == comment.author_id))).scalar_one_or_none()
            project = None
            if task:
                project = (await db.execute(select(Project).where(Project.id == task.project_id))).scalar_one_or_none()
            
            results['comments'].append({
                'id': comment.id,
                'content': comment.content,
                'created_at': comment.created_at,
                'task_id': comment.task_id,
                'task_title': task.title if task else 'Unknown Task',
                'author_name': author.full_name or author.username if author else 'Unknown',
                'project_name': project.name if project else 'Unknown',
                'project_id': project.id if project else None,
            })
    
    return templates.TemplateResponse('search/results.html', {
        'request': request,
        'user': user,
        'query': q,
        'results': results,
        'header_title': f'Search: {q}' if q else 'Search'
    })


# --------------------------
# User Management (all users can view, only admins can create)
# --------------------------
@router.get('/admin/users', response_class=HTMLResponse)
async def web_admin_users_list(
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # All users can view the user list (not just admins)
    # Get all users in the workspace
    users = (
        await db.execute(
            select(User)
            .where(User.workspace_id == user.workspace_id)
            .order_by(User.username)
        )
    ).scalars().all()
    
    return templates.TemplateResponse(
        'admin/users_list.html',
        {
            'request': request,
            'user': user,
            'users': users,
        },
    )


@router.get('/admin/users/create', response_class=HTMLResponse)
async def web_admin_create_user(
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    return templates.TemplateResponse(
        'admin/create_user.html',
        {
            'request': request,
            'user': user,
            'error': None,
            'success': None,
        },
    )


@router.post('/admin/users/create')
async def web_admin_create_user_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    email: str = Form(...),
    is_admin: bool = Form(False),
    db: AsyncSession = Depends(get_session),
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Import validation function
    from app.core.security import validate_password
    
    # Validate password
    is_valid, error_msg = validate_password(password)
    if not is_valid:
        return templates.TemplateResponse(
            'admin/create_user.html',
            {
                'request': request,
                'user': user,
                'error': error_msg,
                'success': None,
            },
            status_code=400
        )
    
    # Check if username already exists
    exists = await db.execute(select(User).where(User.username == username))
    if exists.scalar_one_or_none():
        return templates.TemplateResponse(
            'admin/create_user.html',
            {
                'request': request,
                'user': user,
                'error': 'Username already taken',
                'success': None,
            },
            status_code=400
        )
    
    # Check if email is already used
    if email:
        exists = await db.execute(select(User).where(User.email == email))
        if exists.scalar_one_or_none():
            return templates.TemplateResponse(
                'admin/create_user.html',
                {
                    'request': request,
                    'user': user,
                    'error': 'Email already in use',
                    'success': None,
                },
                status_code=400
            )
    
    # Create new user
    new_user = User(
        username=username,
        hashed_password=get_password_hash(password),
        full_name=full_name,
        email=email,
        workspace_id=user.workspace_id,
        is_admin=is_admin,
        profile_completed=True,  # Admin sets all details
        email_verified=True,
        is_active=True,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    return templates.TemplateResponse(
        'admin/create_user.html',
        {
            'request': request,
            'user': user,
            'error': None,
            'success': f'User "{username}" created successfully!',
        },
    )


@router.post('/admin/users/{user_id}/deactivate')
async def web_admin_deactivate_user(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_session),
):
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    current_user = (await db.execute(select(User).where(User.id == current_user_id))).scalar_one_or_none()
    if not current_user or not current_user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Can't deactivate yourself
    if user_id == current_user_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    
    # Get the user to deactivate
    target_user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Must be in same workspace
    if target_user.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=403, detail="User not in your workspace")
    
    # Deactivate the user
    target_user.is_active = False
    await db.commit()
    
    return RedirectResponse('/web/admin/users', status_code=303)


# User Activity Reports
# --------------------------
@router.get('/admin/reports/user-activity', response_class=HTMLResponse)
async def web_admin_user_activity_report(
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Admin page to generate user activity reports"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Get all users in workspace
    users = (
        await db.execute(
            select(User)
            .where(User.workspace_id == user.workspace_id)
            .order_by(User.full_name, User.username)
        )
    ).scalars().all()
    
    return templates.TemplateResponse(
        'admin/user_activity_report.html',
        {
            'request': request,
            'user': user,
            'users': users,
        },
    )


@router.get('/admin/reports/user-activity/{target_user_id}/pdf')
async def web_admin_generate_user_activity_pdf(
    request: Request,
    target_user_id: int,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_session),
):
    """Generate PDF report of user activity"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Get target user
    target_user = (await db.execute(select(User).where(User.id == target_user_id))).scalar_one_or_none()
    if not target_user or target_user.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Parse date range
    if start_date:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    else:
        start_dt = datetime.now() - timedelta(days=30)
    
    if end_date:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
    else:
        end_dt = datetime.now() + timedelta(days=1)
    
    # Gather activity data
    # 1. Tasks created
    tasks_created = (await db.execute(
        select(Task)
        .where(Task.creator_id == target_user_id)
        .where(Task.created_at >= start_dt)
        .where(Task.created_at < end_dt)
        .order_by(Task.created_at.desc())
    )).scalars().all()
    
    # 2. Task assignments
    task_assignments = (await db.execute(
        select(Task, Assignment)
        .join(Assignment, Task.id == Assignment.task_id)
        .where(Assignment.assignee_id == target_user_id)
        .where(Task.created_at >= start_dt)
        .where(Task.created_at < end_dt)
        .order_by(Task.created_at.desc())
    )).all()
    
    # 3. Task edits
    task_edits = (await db.execute(
        select(TaskHistory)
        .where(TaskHistory.editor_id == target_user_id)
        .where(TaskHistory.created_at >= start_dt)
        .where(TaskHistory.created_at < end_dt)
        .order_by(TaskHistory.created_at.desc())
    )).scalars().all()
    
    # 4. Comments
    comments = (await db.execute(
        select(Comment)
        .where(Comment.author_id == target_user_id)
        .where(Comment.created_at >= start_dt)
        .where(Comment.created_at < end_dt)
        .order_by(Comment.created_at.desc())
    )).scalars().all()
    
    # 5. Projects created
    projects_created = (await db.execute(
        select(Project)
        .where(Project.owner_id == target_user_id)
        .where(Project.created_at >= start_dt)
        .where(Project.created_at < end_dt)
        .order_by(Project.created_at.desc())
    )).scalars().all()
    
    # 6. Activities logged
    activities = (await db.execute(
        select(Activity)
        .where(Activity.created_by == target_user_id)
        .where(Activity.created_at >= start_dt)
        .where(Activity.created_at < end_dt)
        .order_by(Activity.created_at.desc())
    )).scalars().all()
    
    # 7. Tickets closed (newly added)
    from app.models.ticket import Ticket
    tickets_closed = (await db.execute(
        select(Ticket)
        .where(Ticket.closed_by_id == target_user_id)
        .where(Ticket.closed_at >= start_dt)
        .where(Ticket.closed_at < end_dt)
        .order_by(Ticket.closed_at.desc())
    )).scalars().all()
    
    # Generate PDF
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    
    # Create PDF in memory
    import io
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    
    # Container for PDF elements
    elements = []
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1F2937'),
        spaceAfter=30,
        alignment=TA_CENTER,
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=16,
        textColor=colors.HexColor('#374151'),
        spaceAfter=12,
        spaceBefore=20,
    )
    
    subheading_style = ParagraphStyle(
        'CustomSubheading',
        parent=styles['Heading3'],
        fontSize=12,
        textColor=colors.HexColor('#6B7280'),
        spaceAfter=8,
    )
    
    # Title
    elements.append(Paragraph(f"User Activity Report", title_style))
    elements.append(Paragraph(f"{target_user.full_name or target_user.username}", heading_style))
    elements.append(Paragraph(
        f"Period: {start_dt.strftime('%B %d, %Y')} - {end_dt.strftime('%B %d, %Y')}",
        subheading_style
    ))
    elements.append(Paragraph(
        f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
        subheading_style
    ))
    elements.append(Spacer(1, 0.3*inch))
    
    # Summary section
    elements.append(Paragraph("Activity Summary", heading_style))
    summary_data = [
        ['Activity Type', 'Count'],
        ['Tasks Created', str(len(tasks_created))],
        ['Task Assignments', str(len(task_assignments))],
        ['Task Edits', str(len(task_edits))],
        ['Comments Posted', str(len(comments))],
        ['Projects Created', str(len(projects_created))],
        ['Activities Logged', str(len(activities))],
        ['Tickets Closed', str(len(tickets_closed))],
    ]
    
    summary_table = Table(summary_data, colWidths=[4*inch, 2*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3B82F6')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 0.3*inch))
    
    # Tasks Created
    if tasks_created:
        elements.append(Paragraph("Tasks Created", heading_style))
        task_data = [['Date', 'Title', 'Priority', 'Status']]
        for task in tasks_created[:20]:  # Limit to first 20
            task_data.append([
                task.created_at.strftime('%Y-%m-%d %H:%M'),
                task.title[:50],
                task.priority.value.title(),
                task.status.value.replace('_', ' ').title(),
            ])
        
        task_table = Table(task_data, colWidths=[1.5*inch, 2.5*inch, 1*inch, 1.5*inch])
        task_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10B981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        elements.append(task_table)
        elements.append(Spacer(1, 0.2*inch))
    
    # Task Edits
    if task_edits:
        elements.append(Paragraph("Recent Task Edits", heading_style))
        edit_data = [['Date', 'Task ID', 'Field Changed', 'Old Value', 'New Value']]
        for edit in task_edits[:15]:  # Limit to first 15
            edit_data.append([
                edit.created_at.strftime('%Y-%m-%d %H:%M'),
                str(edit.task_id),
                edit.field.replace('_', ' ').title(),
                (edit.old_value or 'None')[:20],
                (edit.new_value or 'None')[:20],
            ])
        
        edit_table = Table(edit_data, colWidths=[1.3*inch, 0.7*inch, 1.2*inch, 1.5*inch, 1.5*inch])
        edit_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F59E0B')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        elements.append(edit_table)
        elements.append(Spacer(1, 0.2*inch))
    
    # Comments
    if comments:
        elements.append(Paragraph("Recent Comments", heading_style))
        comment_data = [['Date', 'Task ID', 'Comment']]
        for comment in comments[:10]:  # Limit to first 10
            comment_data.append([
                comment.created_at.strftime('%Y-%m-%d %H:%M'),
                str(comment.task_id),
                (comment.content or '')[:60],
            ])
        
        comment_table = Table(comment_data, colWidths=[1.5*inch, 0.8*inch, 4*inch])
        comment_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#8B5CF6')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        elements.append(comment_table)
        elements.append(Spacer(1, 0.2*inch))
    
    # Tickets Closed
    if tickets_closed:
        elements.append(Paragraph("Tickets Closed", heading_style))
        ticket_data = [['Date Closed', 'Ticket #', 'Subject', 'Priority']]
        for ticket in tickets_closed[:20]:  # Limit to first 20
            ticket_data.append([
                ticket.closed_at.strftime('%Y-%m-%d %H:%M'),
                ticket.ticket_number,
                ticket.subject[:40],
                ticket.priority.title(),
            ])
        
        ticket_table = Table(ticket_data, colWidths=[1.5*inch, 1.3*inch, 2.5*inch, 1*inch])
        ticket_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EF4444')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        elements.append(ticket_table)
        elements.append(Spacer(1, 0.2*inch))
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    
    # Return PDF file
    from fastapi.responses import StreamingResponse
    filename = f"user_activity_{target_user.username}_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        buffer,
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@router.post('/admin/users/{user_id}/activate')
async def web_admin_activate_user(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_session),
):
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    current_user = (await db.execute(select(User).where(User.id == current_user_id))).scalar_one_or_none()
    if not current_user or not current_user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Get the user to activate
    target_user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Must be in same workspace
    if target_user.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=403, detail="User not in your workspace")
    
    # Activate the user
    target_user.is_active = True
    await db.commit()
    
    return RedirectResponse('/web/admin/users', status_code=303)


@router.post('/admin/users/{user_id}/toggle-admin')
async def web_admin_toggle_admin(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_session),
):
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    current_user = (await db.execute(select(User).where(User.id == current_user_id))).scalar_one_or_none()
    if not current_user or not current_user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Can't modify your own admin rights
    if user_id == current_user_id:
        raise HTTPException(status_code=400, detail="Cannot modify your own admin rights")
    
    # Get the target user
    target_user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Must be in same workspace
    if target_user.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=403, detail="User not in your workspace")
    
    # Toggle admin status
    target_user.is_admin = not target_user.is_admin
    await db.commit()
    
    return RedirectResponse('/web/admin/users', status_code=303)


@router.post('/admin/users/{user_id}/toggle-ticket-visibility')
async def web_admin_toggle_ticket_visibility(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_session),
):
    """Toggle whether a user can see all tickets or only their project tickets"""
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    current_user = (await db.execute(select(User).where(User.id == current_user_id))).scalar_one_or_none()
    if not current_user or not current_user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Get the target user
    target_user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Must be in same workspace
    if target_user.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=403, detail="User not in your workspace")
    
    # Toggle ticket visibility
    target_user.can_see_all_tickets = not target_user.can_see_all_tickets
    await db.commit()
    
    return RedirectResponse('/web/admin/users', status_code=303)


@router.post('/admin/users/{user_id}/delete')
async def web_admin_delete_user(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_session),
):
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    current_user = (await db.execute(select(User).where(User.id == current_user_id))).scalar_one_or_none()
    if not current_user or not current_user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Can't delete yourself
    if user_id == current_user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    
    # Get the user to delete
    target_user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Must be in same workspace
    if target_user.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=403, detail="User not in your workspace")
    
    # Hard delete: Remove user from database
    await db.delete(target_user)
    await db.commit()
    
    return RedirectResponse('/web/admin/users', status_code=303)


@router.post('/admin/users/{user_id}/change-password')
async def web_admin_change_user_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    current_user = (await db.execute(select(User).where(User.id == current_user_id))).scalar_one_or_none()
    if not current_user or not current_user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Get the target user
    target_user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Must be in same workspace
    if target_user.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=403, detail="User not in your workspace")
    
    # Validate password length
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    
    # Hash and update password
    from app.core.security import get_password_hash
    target_user.hashed_password = get_password_hash(new_password)
    
    await db.commit()
    
    return RedirectResponse('/web/admin/users', status_code=303)


# --------------------------
# Admin - Database Backup Management
# --------------------------
@router.get('/admin/backups', response_class=HTMLResponse)
async def web_admin_backups(request: Request, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from app.core.backup import backup_manager
    stats = backup_manager.get_backup_stats()
    
    # Get list of all backups (both .db and .zip)
    backups = []
    for backup_file in sorted(
        [f for f in backup_manager.backup_dir.glob("backup_*.*") 
         if f.suffix in ['.db', '.zip'] and 'latest' not in f.name and 'corrupted' not in f.name],
        key=lambda x: x.stat().st_mtime, 
        reverse=True
    ):
        backup_type = "MANUAL" if "_MANUAL_" in backup_file.name else ("AUTO" if "_AUTO_" in backup_file.name else "UPLOADED")
        includes_attachments = backup_file.suffix == '.zip'
        
        backups.append({
            'filename': backup_file.name,
            'type': backup_type,
            'includes_attachments': includes_attachments,
            'size': backup_file.stat().st_size,
            'size_mb': round(backup_file.stat().st_size / (1024 * 1024), 2),
            'created': datetime.fromtimestamp(backup_file.stat().st_mtime).strftime('%d/%m/%Y %H:%M:%S'),
            'created_timestamp': backup_file.stat().st_mtime
        })
    
    return templates.TemplateResponse('admin/backups.html', {
        'request': request,
        'user': user,
        'stats': stats,
        'backups': backups
    })


@router.post('/admin/backups/create')
async def web_admin_backup_create(
    request: Request, 
    include_attachments: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from app.core.backup import backup_manager
    # Mark as manual backup - won't be auto-deleted
    # Checkbox sends 'on' when checked, None when unchecked
    with_attachments = include_attachments == 'on'
    backup_file = backup_manager.create_backup(is_manual=True, include_attachments=with_attachments)
    
    if backup_file:
        return RedirectResponse('/web/admin/backups?success=backup_created', status_code=303)
    else:
        return RedirectResponse('/web/admin/backups?error=backup_failed', status_code=303)


@router.get('/admin/backups/download/{filename}')
async def web_admin_backup_download(
    request: Request,
    filename: str,
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from app.core.backup import backup_manager
    from fastapi.responses import FileResponse
    
    backup_path = backup_manager.backup_dir / filename
    if not backup_path.exists():
        raise HTTPException(status_code=404, detail="Backup file not found")
    
    return FileResponse(
        path=str(backup_path),
        filename=filename,
        media_type='application/octet-stream'
    )


@router.post('/admin/backups/upload')
async def web_admin_backup_upload(
    request: Request,
    backup_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from app.core.backup import backup_manager
    
    # Validate file extension
    if not backup_file.filename.endswith(('.db', '.zip')):
        return RedirectResponse('/web/admin/backups?error=invalid_file', status_code=303)
    
    # Read file content
    content = await backup_file.read()
    
    # Save the uploaded backup
    saved_path = backup_manager.save_uploaded_backup(content, backup_file.filename)
    
    if saved_path:
        return RedirectResponse('/web/admin/backups?success=backup_uploaded', status_code=303)
    else:
        return RedirectResponse('/web/admin/backups?error=upload_failed', status_code=303)


@router.post('/admin/backups/restore')
async def web_admin_backup_restore(
    request: Request,
    backup_file: str = Form(...),
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from app.core.backup import backup_manager
    from pathlib import Path
    
    backup_path = backup_manager.backup_dir / backup_file
    if not backup_path.exists():
        raise HTTPException(status_code=404, detail="Backup file not found")
    
    success = backup_manager.restore_from_backup(backup_path)
    
    if success:
        return RedirectResponse('/web/admin/backups?success=restore_complete', status_code=303)
    else:
        return RedirectResponse('/web/admin/backups?error=restore_failed', status_code=303)


# --------------------------
# Admin - System Updates
# --------------------------

@router.get('/admin/updates')
async def web_admin_updates(
    request: Request,
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from app.core.update_manager import update_manager
    
    try:
        # Get current version
        current_version = await update_manager.get_current_version()
        
        # Get commit history
        commit_history = await update_manager.get_commit_history(limit=30)
    except Exception as e:
        # If update manager fails, provide defaults
        current_version = {
            'hash': 'unknown',
            'message': 'Unable to determine version',
            'date': 'unknown',
            'branch': 'unknown'
        }
        commit_history = []
    
    return templates.TemplateResponse('admin/updates.html', {
        'request': request,
        'user': user,
        'current_version': current_version,
        'commit_history': commit_history,
        'success': request.query_params.get('success'),
        'error': request.query_params.get('error')
    })


@router.post('/admin/updates/latest')
async def web_admin_update_latest(
    request: Request,
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from app.core.update_manager import update_manager
    
    result = await update_manager.update_to_latest()
    
    if result["success"]:
        # Restart service after update
        await update_manager.restart_service()
        return RedirectResponse('/web/admin/updates?success=update_complete', status_code=303)
    else:
        error_msg = result.get("error", "Unknown error")
        return RedirectResponse(f'/web/admin/updates?error={error_msg}', status_code=303)


@router.post('/admin/updates/rollback')
async def web_admin_update_rollback(
    request: Request,
    commit_hash: str = Form(...),
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from app.core.update_manager import update_manager
    
    result = await update_manager.rollback_to_commit(commit_hash)
    
    if result["success"]:
        # Restart service after rollback
        await update_manager.restart_service()
        return RedirectResponse('/web/admin/updates?success=rollback_complete', status_code=303)
    else:
        error_msg = result.get("error", "Unknown error")
        return RedirectResponse(f'/web/admin/updates?error={error_msg}', status_code=303)


# --------------------------
# Admin - Email Settings
# --------------------------
@router.get('/admin/email-settings', response_class=HTMLResponse)
async def web_admin_email_settings(request: Request, db: AsyncSession = Depends(get_session)):
    """Admin page to configure email settings"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from app.models.email_settings import EmailSettings
    
    # Get existing settings
    settings = (await db.execute(
        select(EmailSettings).where(EmailSettings.workspace_id == user.workspace_id)
    )).scalar_one_or_none()
    
    default_body = """Dear {guest_name} {guest_surname},

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
This is an automated message. Please do not reply to this email."""
    
    return templates.TemplateResponse('admin/email_settings.html', {
        'request': request,
        'user': user,
        'settings': settings,
        'default_body': default_body
    })


@router.post('/admin/email-settings/save')
async def web_admin_email_settings_save(
    request: Request,
    smtp_host: str = Form(...),
    smtp_port: int = Form(...),
    smtp_username: str = Form(...),
    smtp_password: str = Form(...),
    smtp_from_email: str = Form(...),
    smtp_from_name: str = Form(...),
    smtp_use_tls: Optional[str] = Form(None),
    incoming_mail_type: Optional[str] = Form("POP3"),
    incoming_mail_host: Optional[str] = Form(None),
    incoming_mail_port: Optional[int] = Form(110),
    incoming_mail_username: Optional[str] = Form(None),
    incoming_mail_password: Optional[str] = Form(None),
    incoming_mail_use_ssl: Optional[str] = Form(None),
    webmail_url: Optional[str] = Form(None),
    confirmation_subject: str = Form(...),
    confirmation_body: str = Form(...),
    company_name: str = Form(...),
    auto_reply_enabled: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session)
):
    """Save email settings"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from app.models.email_settings import EmailSettings
    from datetime import datetime
    
    try:
        # Get or create settings
        settings = (await db.execute(
            select(EmailSettings).where(EmailSettings.workspace_id == user.workspace_id)
        )).scalar_one_or_none()
        
        if settings:
            # Update existing
            settings.smtp_host = smtp_host
            settings.smtp_port = smtp_port
            settings.smtp_username = smtp_username
            settings.smtp_password = smtp_password
            settings.smtp_from_email = smtp_from_email
            settings.smtp_from_name = smtp_from_name
            settings.smtp_use_tls = smtp_use_tls == 'true'
            settings.incoming_mail_type = incoming_mail_type
            settings.incoming_mail_host = incoming_mail_host
            settings.incoming_mail_port = incoming_mail_port
            settings.incoming_mail_username = incoming_mail_username
            settings.incoming_mail_password = incoming_mail_password
            settings.incoming_mail_use_ssl = incoming_mail_use_ssl == 'true'
            settings.webmail_url = webmail_url
            settings.confirmation_subject = confirmation_subject
            settings.confirmation_body = confirmation_body
            settings.company_name = company_name
            settings.auto_reply_enabled = auto_reply_enabled == 'true'
            settings.updated_at = datetime.utcnow()
        else:
            # Create new
            settings = EmailSettings(
                workspace_id=user.workspace_id,
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_username=smtp_username,
                smtp_password=smtp_password,
                smtp_from_email=smtp_from_email,
                smtp_from_name=smtp_from_name,
                smtp_use_tls=smtp_use_tls == 'true',
                incoming_mail_type=incoming_mail_type,
                incoming_mail_host=incoming_mail_host,
                incoming_mail_port=incoming_mail_port,
                incoming_mail_username=incoming_mail_username,
                incoming_mail_password=incoming_mail_password,
                incoming_mail_use_ssl=incoming_mail_use_ssl == 'true',
                webmail_url=webmail_url,
                confirmation_subject=confirmation_subject,
                confirmation_body=confirmation_body,
                company_name=company_name,
                auto_reply_enabled=auto_reply_enabled == 'true'
            )
            db.add(settings)
        
        await db.commit()
        
        request.session['flash_message'] = "✓ Email settings saved successfully"
        request.session['flash_type'] = 'success'
        
    except Exception as e:
        request.session['flash_message'] = f"✗ Failed to save settings: {str(e)}"
        request.session['flash_type'] = 'error'
    
    return RedirectResponse('/web/admin/email-settings', status_code=303)


@router.post('/admin/email-settings/test')
async def web_admin_email_settings_test(request: Request, db: AsyncSession = Depends(get_session)):
    """Send test email"""
    user_id = request.session.get('user_id')
    if not user_id:
        return JSONResponse({'success': False, 'error': 'Not authenticated'})
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        return JSONResponse({'success': False, 'error': 'Admin access required'})
    
    from app.models.email_settings import EmailSettings
    import smtplib
    from email.mime.text import MIMEText
    
    try:
        settings = (await db.execute(
            select(EmailSettings).where(EmailSettings.workspace_id == user.workspace_id)
        )).scalar_one_or_none()
        
        if not settings:
            return JSONResponse({'success': False, 'error': 'Email settings not configured'})
        
        # Send test email
        msg = MIMEText("This is a test email from your CRM system. Email settings are working correctly!")
        msg['Subject'] = "Test Email - CRM System"
        msg['From'] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
        msg['To'] = settings.smtp_username
        
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port)
        if settings.smtp_use_tls:
            server.starttls()
        server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)
        server.quit()
        
        return JSONResponse({'success': True})
        
    except Exception as e:
        return JSONResponse({'success': False, 'error': str(e)})


@router.post('/admin/email-settings/check-emails')
async def web_admin_check_emails(request: Request, db: AsyncSession = Depends(get_session)):
    """Manually trigger email check (for testing)"""
    user_id = request.session.get('user_id')
    if not user_id:
        return JSONResponse({'success': False, 'error': 'Not authenticated'})
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        return JSONResponse({'success': False, 'error': 'Admin access required'})
    
    try:
        from app.core.email_to_ticket_v2 import process_workspace_emails
        
        # Process emails for this workspace
        tickets = await process_workspace_emails(db, user.workspace_id)
        
        return JSONResponse({
            'success': True, 
            'message': f'Checked emails successfully',
            'tickets_created': len(tickets),
            'ticket_numbers': [t.ticket_number for t in tickets]
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Email check error: {error_details}")
        return JSONResponse({'success': False, 'error': str(e), 'details': error_details})


@router.get('/admin/email-settings/debug')
async def web_admin_debug_settings(request: Request, db: AsyncSession = Depends(get_session)):
    """Debug: Show current email settings from database"""
    user_id = request.session.get('user_id')
    if not user_id:
        return JSONResponse({'success': False, 'error': 'Not authenticated'})
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        return JSONResponse({'success': False, 'error': 'Admin access required'})
    
    from app.models.email_settings import EmailSettings
    from app.models.processed_mail import ProcessedMail
    
    settings = (await db.execute(
        select(EmailSettings).where(EmailSettings.workspace_id == user.workspace_id)
    )).scalar_one_or_none()
    
    if not settings:
        return JSONResponse({'success': False, 'error': 'No email settings found'})
    
    # Get recent processedmail entries
    processed_emails = (await db.execute(
        select(ProcessedMail)
        .where(ProcessedMail.workspace_id == user.workspace_id)
        .order_by(ProcessedMail.processed_at.desc())
        .limit(10)
    )).scalars().all()
    
    return JSONResponse({
        'success': True,
        'settings': {
            'smtp_host': settings.smtp_host,
            'smtp_port': settings.smtp_port,
            'smtp_username': settings.smtp_username,
            'incoming_mail_host': settings.incoming_mail_host,
            'incoming_mail_port': settings.incoming_mail_port,
            'incoming_mail_username': settings.incoming_mail_username,
            'incoming_mail_use_ssl': settings.incoming_mail_use_ssl,
            'mail_type': settings.mail_type,
            'workspace_id': settings.workspace_id
        },
        'processed_emails': [{
            'message_id': p.message_id[:50] + '...' if len(p.message_id) > 50 else p.message_id,
            'email_from': p.email_from,
            'subject': p.subject,
            'ticket_id': p.ticket_id,
            'processed_at': p.processed_at.isoformat() if p.processed_at else None
        } for p in processed_emails]
    })


@router.get('/admin/email-settings/preview-inbox')
async def web_admin_preview_inbox(request: Request, db: AsyncSession = Depends(get_session)):
    """Preview inbox emails without processing them"""
    user_id = request.session.get('user_id')
    if not user_id:
        return JSONResponse({'success': False, 'error': 'Not authenticated'})
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        return JSONResponse({'success': False, 'error': 'Admin access required'})
    
    try:
        from app.models.email_settings import EmailSettings
        import imaplib
        import email
        from email.header import decode_header
        from datetime import datetime
        
        # Get email settings
        settings = (await db.execute(
            select(EmailSettings).where(EmailSettings.workspace_id == user.workspace_id)
        )).scalar_one_or_none()
        
        if not settings or not settings.incoming_mail_host:
            return JSONResponse({
                'success': False, 
                'error': 'Incoming mail not configured',
                'details': 'Please configure IMAP settings first'
            })
        
        # Check if mail type is IMAP
        if settings.mail_type and settings.mail_type.upper() != 'IMAP':
            return JSONResponse({
                'success': False,
                'error': f'Mail type is set to {settings.mail_type}',
                'details': 'Inbox preview only works with IMAP. Change mail type to IMAP in settings.'
            })
        
        # Connect to IMAP
        mail = None
        try:
            if settings.incoming_mail_use_ssl:
                print(f"[Preview] Connecting to {settings.incoming_mail_host}:{settings.incoming_mail_port or 993} (SSL)")
                mail = imaplib.IMAP4_SSL(
                    settings.incoming_mail_host,
                    settings.incoming_mail_port or 993,
                    timeout=10
                )
            else:
                print(f"[Preview] Connecting to {settings.incoming_mail_host}:{settings.incoming_mail_port or 143} (no SSL)")
                mail = imaplib.IMAP4(
                    settings.incoming_mail_host,
                    settings.incoming_mail_port or 143,
                    timeout=10
                )
            
            print(f"[Preview] Logging in as {settings.incoming_mail_username}")
            mail.login(settings.incoming_mail_username, settings.incoming_mail_password)
        except (imaplib.IMAP4.error, OSError, TimeoutError) as e:
            error_msg = str(e) if str(e) else 'Connection refused or timeout'
            return JSONResponse({
                'success': False,
                'error': f'IMAP connection failed: {error_msg}',
                'details': f'Could not connect to {settings.incoming_mail_host}:{settings.incoming_mail_port or (993 if settings.incoming_mail_use_ssl else 143)}. Verify server is reachable and port is correct.'
            })
        except Exception as e:
            return JSONResponse({
                'success': False,
                'error': f'Connection error: {str(e)}',
                'details': 'Verify IMAP server settings and credentials'
            })
        mail.select('INBOX')
        
        # Search for last 10 emails (ALL, not just UNSEEN)
        status, messages = mail.search(None, 'ALL')
        email_ids = messages[0].split()
        
        # Get last 10 emails
        email_ids = email_ids[-10:] if len(email_ids) > 10 else email_ids
        email_ids = reversed(email_ids)  # Show newest first
        
        emails = []
        for email_id in email_ids:
            try:
                # Fetch email headers and body
                status, msg_data = mail.fetch(email_id, '(RFC822 FLAGS)')
                flags = msg_data[0]
                msg = email.message_from_bytes(msg_data[0][1])
                
                # Check if unread
                is_unread = b'\\Seen' not in flags
                
                # Decode subject
                subject_header = msg.get('Subject', '')
                if subject_header:
                    decoded_parts = decode_header(subject_header)
                    subject = ''
                    for part, encoding in decoded_parts:
                        if isinstance(part, bytes):
                            subject += part.decode(encoding or 'utf-8', errors='ignore')
                        else:
                            subject += part
                else:
                    subject = '(No Subject)'
                
                # Get from
                from_header = msg.get('From', 'Unknown')
                
                # Get date
                date_header = msg.get('Date', '')
                try:
                    date_obj = email.utils.parsedate_to_datetime(date_header)
                    date_str = date_obj.strftime('%b %d, %H:%M')
                except:
                    date_str = date_header[:20] if date_header else 'Unknown'
                
                # Get In-Reply-To
                in_reply_to = msg.get('In-Reply-To', '')
                
                # Get body preview
                body = ''
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == 'text/plain':
                            try:
                                body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                break
                            except:
                                pass
                else:
                    try:
                        body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                    except:
                        body = str(msg.get_payload())
                
                # Clean body for preview
                body = body.replace('\r', '').replace('\n', ' ').strip()
                
                emails.append({
                    'subject': subject,
                    'from': from_header,
                    'date': date_str,
                    'is_unread': is_unread,
                    'in_reply_to': in_reply_to,
                    'preview': body[:200] if body else None
                })
                
            except Exception as e:
                print(f"Error fetching email {email_id}: {e}")
                continue
        
        mail.close()
        mail.logout()
        
        return JSONResponse({
            'success': True,
            'emails': emails,
            'total': len(emails)
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Inbox preview error: {error_details}")
        return JSONResponse({
            'success': False, 
            'error': str(e),
            'details': error_details
        })


# --------------------------
# Site Settings (Admin Only)
# --------------------------
@router.get('/admin/site-settings', response_class=HTMLResponse)
async def web_admin_site_settings(
    request: Request,
    db: AsyncSession = Depends(get_session)
):
    """Site branding and customization settings"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        return RedirectResponse('/web/dashboard', status_code=303)
    
    # Get workspace settings
    workspace = (await db.execute(
        select(Workspace).where(Workspace.id == user.workspace_id)
    )).scalar_one_or_none()
    
    success_message = request.session.pop('success_message', None)
    error_message = request.session.pop('error_message', None)
    
    return templates.TemplateResponse('admin/site_settings.html', {
        'request': request,
        'user': user,
        'workspace': workspace,
        'success_message': success_message,
        'error_message': error_message
    })


@router.post('/admin/site-settings/save')
async def web_admin_site_settings_save(
    request: Request,
    site_title: str = Form(None),
    primary_color: str = Form("#2563eb"),
    db: AsyncSession = Depends(get_session)
):
    """Save site branding settings"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        return RedirectResponse('/web/dashboard', status_code=303)
    
    try:
        # Update workspace settings
        workspace = (await db.execute(
            select(Workspace).where(Workspace.id == user.workspace_id)
        )).scalar_one_or_none()
        
        if workspace:
            workspace.site_title = site_title if site_title else None
            workspace.primary_color = primary_color
            
            await db.commit()
            request.session['success_message'] = 'Site settings saved successfully!'
        else:
            request.session['error_message'] = 'Workspace not found'
            
    except Exception as e:
        request.session['error_message'] = f'Failed to save settings: {str(e)}'
    
    return RedirectResponse('/web/admin/site-settings', status_code=303)


@router.post('/admin/site-settings/upload-logo')
async def web_admin_site_settings_upload_logo(
    request: Request,
    logo: UploadFile = File(...),
    db: AsyncSession = Depends(get_session)
):
    """Upload site logo"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        return RedirectResponse('/web/dashboard', status_code=303)
    
    try:
        # Validate file type
        allowed_types = ['image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/svg+xml']
        if logo.content_type not in allowed_types:
            request.session['error_message'] = 'Invalid file type. Please upload PNG, JPG, GIF, or SVG.'
            return RedirectResponse('/web/admin/site-settings', status_code=303)
        
        # Create uploads directory if it doesn't exist
        import os
        uploads_dir = os.path.join(os.getcwd(), 'app', 'uploads', 'branding')
        os.makedirs(uploads_dir, exist_ok=True)
        
        # Generate unique filename
        import uuid
        from pathlib import Path
        file_extension = Path(logo.filename).suffix
        filename = f"logo_{uuid.uuid4().hex}{file_extension}"
        file_path = os.path.join(uploads_dir, filename)
        
        # Save file
        with open(file_path, 'wb') as f:
            content = await logo.read()
            f.write(content)
        
        # Update workspace
        workspace = (await db.execute(
            select(Workspace).where(Workspace.id == user.workspace_id)
        )).scalar_one_or_none()
        
        if workspace:
            # Delete old logo if exists
            if workspace.logo_url:
                old_path = os.path.join(os.getcwd(), 'app', workspace.logo_url.lstrip('/'))
                if os.path.exists(old_path):
                    os.remove(old_path)
            
            workspace.logo_url = f"/uploads/branding/{filename}"
            await db.commit()
            request.session['success_message'] = 'Logo uploaded successfully!'
        
    except Exception as e:
        request.session['error_message'] = f'Failed to upload logo: {str(e)}'
    
    return RedirectResponse('/web/admin/site-settings', status_code=303)


# --------------------------
# My tasks view
# --------------------------
@router.get('/my-tasks', response_class=HTMLResponse)
async def web_my_tasks(
    request: Request,
    assignee_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_session),
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)

    # Convert empty strings to None for integer filters
    assignee_id_int = int(assignee_id) if assignee_id and assignee_id.strip() else None
    project_id_int = int(project_id) if project_id and project_id.strip() else None

    # Non-admin: only tasks assigned to me with optional filters
    if not user.is_admin:
        stmt = (
            select(Task)
            .join(Project, Task.project_id == Project.id)
            .join(Assignment, Assignment.task_id == Task.id)
            .where(Assignment.assignee_id == user_id, Project.workspace_id == user.workspace_id)
        )
        if status and status.strip():
            try:
                st = TaskStatus(status)
                stmt = stmt.where(Task.status == st)
            except Exception:
                pass
        if project_id_int:
            stmt = stmt.where(Task.project_id == project_id_int)
        stmt = stmt.order_by(Task.created_at.desc())
        tasks = (await db.execute(stmt)).scalars().all()
        # Non-admin users only see projects they're assigned to
        from app.models.project_member import ProjectMember
        projects = (
            await db.execute(
                select(Project)
                .join(ProjectMember, Project.id == ProjectMember.project_id)
                .where(
                    ProjectMember.user_id == user_id,
                    Project.workspace_id == user.workspace_id
                )
                .order_by(Project.name)
            )
        ).scalars().all()
        return templates.TemplateResponse(
            'tasks/my.html',
            {
                'request': request,
                'tasks': tasks,
                'is_admin': False,
                'projects': projects,
                'selected': {'status': status, 'project_id': project_id},
            },
        )

    # Admin: show all assigned tasks in the workspace, with assignees listed
    tasks_stmt = (
        select(Task)
        .join(Project, Task.project_id == Project.id)
        .join(Assignment, Assignment.task_id == Task.id)
        .where(Project.workspace_id == user.workspace_id)
    )
    if assignee_id_int:
        tasks_stmt = tasks_stmt.where(Assignment.assignee_id == assignee_id_int)
    if status and status.strip():
        try:
            st = TaskStatus(status)
            tasks_stmt = tasks_stmt.where(Task.status == st)
        except Exception:
            pass
    if project_id_int:
        tasks_stmt = tasks_stmt.where(Task.project_id == project_id_int)
    tasks_stmt = tasks_stmt.order_by(Task.created_at.desc())
    tasks = (await db.execute(tasks_stmt)).scalars().all()

    # Build assignees map {task_id: ["Name or Email", ...]}
    assocs = (
        await db.execute(
            select(Assignment.task_id, User.full_name, User.email)
            .join(User, Assignment.assignee_id == User.id)
            .join(Task, Assignment.task_id == Task.id)
            .join(Project, Task.project_id == Project.id)
            .where(Project.workspace_id == user.workspace_id)
        )
    ).all()
    assignees_map: dict[int, list[str]] = {}
    for task_id_val, full_name, email in assocs:
        label = (full_name or '').strip() or email
        assignees_map.setdefault(task_id_val, []).append(label)

    users = (
        await db.execute(select(User).where(User.workspace_id == user.workspace_id, User.is_active == True).order_by(User.full_name, User.email))
    ).scalars().all()
    projects = (
        await db.execute(select(Project).where(Project.workspace_id == user.workspace_id).order_by(Project.name))
    ).scalars().all()
    return templates.TemplateResponse(
        'tasks/my.html',
        {
            'request': request,
            'tasks': tasks,
            'is_admin': True,
            'assignees_map': assignees_map,
            'users': users,
            'projects': projects,
            'selected': {'assignee_id': assignee_id, 'status': status, 'project_id': project_id},
        },
    )

# --------------------------
# Projects (minimal to enable navigation)
# --------------------------
@router.get('/projects', response_class=HTMLResponse)
async def web_projects(request: Request, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Admin: see all projects in workspace (excluding archived)
    # Regular user: only see projects they're assigned to (excluding archived)
    if user.is_admin:
        result = await db.execute(
            select(Project)
            .where(Project.workspace_id == user.workspace_id, Project.is_archived == False)
            .order_by(Project.created_at.desc())
        )
        projects = result.scalars().all()
    else:
        from app.models.project_member import ProjectMember
        result = await db.execute(
            select(Project)
            .join(ProjectMember, Project.id == ProjectMember.project_id)
            .where(
                ProjectMember.user_id == user_id,
                Project.workspace_id == user.workspace_id,
                Project.is_archived == False
            )
            .order_by(Project.created_at.desc())
        )
        projects = result.scalars().all()
    
    return templates.TemplateResponse('projects/index.html', {
        'request': request, 
        'user': user,
        'projects': projects
    })


@router.post('/projects/create')
async def web_projects_create(request: Request, name: str = Form(...), description: Optional[str] = Form(None), db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    project = Project(name=name, description=description, owner_id=user_id, workspace_id=user.workspace_id)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    
    # Auto-assign the creator to the project
    from app.models.project_member import ProjectMember
    member = ProjectMember(project_id=project.id, user_id=user_id, assigned_by=user_id)
    db.add(member)
    await db.commit()
    
    return RedirectResponse('/web/projects', status_code=303)


@router.post('/projects/{project_id}/edit')
async def web_projects_edit(request: Request, project_id: int, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Only admins can edit projects
    if not user.is_admin:
        return RedirectResponse('/web/projects', status_code=303)
    
    # Get the project
    result = await db.execute(select(Project).where(Project.id == project_id, Project.workspace_id == user.workspace_id))
    project = result.scalar_one_or_none()
    
    if project:
        form = await request.form()
        project.name = form.get('name', project.name)
        project.description = form.get('description') or None
        project.support_email = form.get('support_email') or None
        await db.commit()
    
    return RedirectResponse('/web/projects', status_code=303)


@router.post('/projects/{project_id}/delete')
async def web_projects_delete(request: Request, project_id: int, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Only admins can delete projects
    if not user.is_admin:
        return RedirectResponse('/web/projects', status_code=303)
    
    # Get the project
    result = await db.execute(select(Project).where(Project.id == project_id, Project.workspace_id == user.workspace_id))
    project = result.scalar_one_or_none()
    
    if project:
        await db.delete(project)
        await db.commit()
    
    return RedirectResponse('/web/projects', status_code=303)


@router.post('/projects/{project_id}/archive')
async def web_projects_archive(request: Request, project_id: int, db: AsyncSession = Depends(get_session)):
    """Archive a project - preserves all data, comments, and attachments"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Only admins can archive projects
    if not user.is_admin:
        return RedirectResponse('/web/projects', status_code=303)
    
    # Get the project
    result = await db.execute(select(Project).where(Project.id == project_id, Project.workspace_id == user.workspace_id))
    project = result.scalar_one_or_none()
    
    if project:
        from datetime import datetime
        project.is_archived = True
        project.archived_at = datetime.utcnow()
        await db.commit()
    
    return RedirectResponse('/web/projects', status_code=303)


@router.post('/projects/{project_id}/restore')
async def web_projects_restore(request: Request, project_id: int, db: AsyncSession = Depends(get_session)):
    """Restore an archived project"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Only admins can restore projects
    if not user.is_admin:
        return RedirectResponse('/web/projects/archived', status_code=303)
    
    # Get the project
    result = await db.execute(select(Project).where(Project.id == project_id, Project.workspace_id == user.workspace_id))
    project = result.scalar_one_or_none()
    
    if project:
        project.is_archived = False
        project.archived_at = None
        await db.commit()
    
    return RedirectResponse('/web/projects/archived', status_code=303)


@router.get('/projects/archived', response_class=HTMLResponse)
async def web_projects_archived(request: Request, db: AsyncSession = Depends(get_session)):
    """View archived projects"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Get archived projects
    if user.is_admin:
        result = await db.execute(
            select(Project)
            .where(Project.workspace_id == user.workspace_id, Project.is_archived == True)
            .order_by(Project.archived_at.desc())
        )
        projects = result.scalars().all()
    else:
        from app.models.project_member import ProjectMember
        result = await db.execute(
            select(Project)
            .join(ProjectMember, Project.id == ProjectMember.project_id)
            .where(
                ProjectMember.user_id == user_id,
                Project.workspace_id == user.workspace_id,
                Project.is_archived == True
            )
            .order_by(Project.archived_at.desc())
        )
        projects = result.scalars().all()
    
    return templates.TemplateResponse('projects/archived.html', {
        'request': request, 
        'user': user,
        'projects': projects
    })


# --------------------------
# Project Members (Admin only)
# --------------------------
@router.get('/projects/{project_id}/members', response_class=HTMLResponse)
async def web_project_members(request: Request, project_id: int, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail='Admin access required')
    
    # Get project
    project = (await db.execute(
        select(Project).where(Project.id == project_id, Project.workspace_id == user.workspace_id)
    )).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail='Project not found')
    
    # Get assigned members (exclude deleted users)
    from app.models.project_member import ProjectMember
    assigned_members = (await db.execute(
        select(User, ProjectMember.assigned_at)
        .join(ProjectMember, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(User.full_name, User.email)
    )).all()
    
    # Get all active workspace users for assignment dropdown
    all_users = (await db.execute(
        select(User)
        .where(User.workspace_id == user.workspace_id)
        .where(User.is_active == True)
        .order_by(User.full_name, User.email)
    )).scalars().all()
    
    # Filter out already assigned users
    assigned_user_ids = {m[0].id for m in assigned_members}
    available_users = [u for u in all_users if u.id not in assigned_user_ids]
    
    return templates.TemplateResponse('projects/members.html', {
        'request': request,
        'user': user,
        'project': project,
        'assigned_members': assigned_members,
        'available_users': available_users
    })


@router.post('/projects/{project_id}/members/add')
async def web_project_members_add(
    request: Request, 
    project_id: int, 
    user_identifier: str = Form(...),  # Changed from user_id_to_add to user_identifier 
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail='Admin access required')
    
    # Verify project exists in workspace
    project = (await db.execute(
        select(Project).where(Project.id == project_id, Project.workspace_id == user.workspace_id)
    )).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail='Project not found')
    
    # Find user by username or email in the same workspace
    user_identifier = user_identifier.strip()
    target_user = (await db.execute(
        select(User).where(
            User.workspace_id == user.workspace_id,
            (User.username == user_identifier) | (User.email == user_identifier)
        )
    )).scalar_one_or_none()
    
    if not target_user:
        # User not found - return with error message
        request.session['error_message'] = f'User with username or email "{user_identifier}" not found in your workspace'
        return RedirectResponse(f'/web/projects/{project_id}/members', status_code=303)
    
    # Check if already assigned
    from app.models.project_member import ProjectMember
    existing = (await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == target_user.id
        )
    )).scalar_one_or_none()
    
    if existing:
        request.session['info_message'] = f'User {target_user.full_name or target_user.email} is already assigned to this project'
    else:
        member = ProjectMember(project_id=project_id, user_id=target_user.id, assigned_by=user_id)
        db.add(member)
        await db.commit()
        request.session['success_message'] = f'Successfully added {target_user.full_name or target_user.email} to the project'
    
    return RedirectResponse(f'/web/projects/{project_id}/members', status_code=303)


@router.post('/projects/{project_id}/members/{member_user_id}/remove')
async def web_project_members_remove(
    request: Request, 
    project_id: int, 
    member_user_id: int, 
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail='Admin access required')
    
    # Remove the assignment
    from app.models.project_member import ProjectMember
    member = (await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == member_user_id
        )
    )).scalar_one_or_none()
    
    if member:
        await db.delete(member)
        await db.commit()
    
    return RedirectResponse(f'/web/projects/{project_id}/members', status_code=303)


# Project detail + simple Kanban
@router.get('/projects/{project_id}', response_class=HTMLResponse)
async def web_project_detail(request: Request, project_id: int, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    result = await db.execute(select(Project).where(Project.id == project_id, Project.workspace_id == user.workspace_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail='Project not found')
    
    # Check if user has access to this project (admin or assigned member)
    if not user.is_admin:
        from app.models.project_member import ProjectMember
        member = (await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id
            )
        )).scalar_one_or_none()
        if not member:
            raise HTTPException(status_code=403, detail='You do not have access to this project')
    # Fetch only non-archived tasks for board view (archived tasks go to the Done tab in tasks/list)
    tasks_result = await db.execute(
        select(Task).where(Task.project_id == project_id, Task.is_archived == False)
    )
    tasks = tasks_result.scalars().all()
    
    # Organize tasks by status for kanban columns
    columns = {
        TaskStatus.todo: [],
        TaskStatus.in_progress: [],
        TaskStatus.blocked: [],
        TaskStatus.done: []
    }
    for t in tasks:
        if t.status in columns:
            columns[t.status].append(t)
    
    # Build assignees map: {task_id: [(full_name, email), ...]}
    assignees_map = {}
    if tasks:
        task_ids = [t.id for t in tasks]
        assignments = (await db.execute(
            select(Assignment.task_id, User.full_name, User.email)
            .join(User, User.id == Assignment.assignee_id)
            .where(Assignment.task_id.in_(task_ids))
        )).all()
        for task_id, full_name, email in assignments:
            if task_id not in assignees_map:
                assignees_map[task_id] = []
            assignees_map[task_id].append((full_name or email, email))
    
    # Fetch subtasks for all tasks
    from app.models.subtask import Subtask
    subtasks_map = {}  # {task_id: [subtasks]}
    subtask_stats = {}  # {task_id: {'total': x, 'completed': y}}
    if tasks:
        task_ids = [t.id for t in tasks]
        subtasks = (await db.execute(
            select(Subtask).where(Subtask.task_id.in_(task_ids)).order_by(Subtask.order)
        )).scalars().all()
        
        for subtask in subtasks:
            if subtask.task_id not in subtasks_map:
                subtasks_map[subtask.task_id] = []
                subtask_stats[subtask.task_id] = {'total': 0, 'completed': 0}
            subtasks_map[subtask.task_id].append(subtask)
            subtask_stats[subtask.task_id]['total'] += 1
            if subtask.is_completed:
                subtask_stats[subtask.task_id]['completed'] += 1
    
    # Fetch all active users in workspace for assignment dropdown
    users = (await db.execute(select(User).where(User.workspace_id == user.workspace_id, User.is_active == True).order_by(User.full_name, User.email))).scalars().all()
    return templates.TemplateResponse('projects/detail.html', {
        'request': request, 
        'project': project, 
        'tasks': tasks, 
        'TaskStatus': TaskStatus, 
        'columns': columns,
        'assignees_map': assignees_map,
        'subtasks_map': subtasks_map,
        'subtask_stats': subtask_stats,
        'users': users,
        'user': user
    })


# Tasks
@router.post('/tasks/create')
async def web_task_create(request: Request, project_id: int = Form(...), title: str = Form(...), description: Optional[str] = Form(None), subtasks: Optional[str] = Form(None), priority: str = Form('medium'), start_date_value: Optional[str] = Form(None), start_time_value: Optional[str] = Form(None), due_date_value: Optional[str] = Form(None), due_time_value: Optional[str] = Form(None), db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    # Ensure project belongs to user's workspace
    project = (await db.execute(select(Project).where(Project.id == project_id, Project.workspace_id == user.workspace_id))).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail='Project not found')
    
    # Ensure user has access to this project (admin or assigned member)
    if not user.is_admin:
        from app.models.project_member import ProjectMember
        member = (await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id
            )
        )).scalar_one_or_none()
        if not member:
            raise HTTPException(status_code=403, detail='You do not have access to this project')
    
    # Parse dates and times
    from datetime import date, time
    start_date = date.fromisoformat(start_date_value) if start_date_value else None
    start_time_obj = time.fromisoformat(start_time_value) if start_time_value else None
    due_date = date.fromisoformat(due_date_value) if due_date_value else None
    due_time_obj = time.fromisoformat(due_time_value) if due_time_value else None
    
    # Parse priority
    from app.models.enums import TaskPriority
    try:
        task_priority = TaskPriority(priority)
    except ValueError:
        task_priority = TaskPriority.medium
    
    task = Task(
        title=title, 
        description=description, 
        project_id=project_id,
        creator_id=user_id,
        priority=task_priority,
        start_date=start_date,
        start_time=start_time_obj,
        due_date=due_date,
        due_time=due_time_obj
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    
    # Create subtasks if provided
    if subtasks:
        from app.models.subtask import Subtask
        subtask_titles = [title.strip() for title in subtasks.split('\n') if title.strip()]
        for index, subtask_title in enumerate(subtask_titles):
            new_subtask = Subtask(
                task_id=task.id,
                title=subtask_title,
                is_completed=False,
                order=index
            )
            db.add(new_subtask)
        await db.commit()
    
    # Auto-assign task to creator if they're not an admin
    if not user.is_admin:
        from app.models.assignment import Assignment
        assignment = Assignment(task_id=task.id, assignee_id=user_id, assigner_id=user_id)
        db.add(assignment)
        await db.commit()
    
    return RedirectResponse(f'/web/projects/{project_id}', status_code=303)


@router.get('/tasks/list')
async def web_tasks_list(
    request: Request,
    tab: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    assignee_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    from datetime import date
    
    # Convert empty strings to None for integer filters
    assignee_id_int = int(assignee_id) if assignee_id and assignee_id.strip() else None
    project_id_int = int(project_id) if project_id and project_id.strip() else None
    
    # Build query with filters
    # Admin: see all tasks in workspace
    # Regular user: only tasks from projects they're assigned to
    if user.is_admin:
        query = (
            select(Task, Project.name.label('project_name'))
            .join(Project, Task.project_id == Project.id)
            .where(Project.workspace_id == user.workspace_id)
        )
    else:
        from app.models.project_member import ProjectMember
        query = (
            select(Task, Project.name.label('project_name'))
            .join(Project, Task.project_id == Project.id)
            .join(ProjectMember, Project.id == ProjectMember.project_id)
            .where(
                ProjectMember.user_id == user_id,
                Project.workspace_id == user.workspace_id
            )
        )
    
    # Tab filtering - only apply if no specific status filter is set
    if not status or not status.strip():
        if tab == 'done':
            query = query.where(Task.status == 'done')
        else:  # Active tasks (default when tab is None, empty, or 'active')
            query = query.where(Task.status.in_(['todo', 'in_progress', 'blocked']))
    
    if status and status.strip():
        query = query.where(Task.status == status)
    if priority and priority.strip():
        query = query.where(Task.priority == priority)
    if project_id_int:
        query = query.where(Task.project_id == project_id_int)
    if assignee_id_int:
        query = query.join(Assignment, Task.id == Assignment.task_id).where(Assignment.assignee_id == assignee_id_int)
    
    query = query.order_by(Task.due_date.asc().nullslast(), Task.priority.desc())
    
    results = (await db.execute(query)).all()
    tasks = []
    project_names = {}
    for task, project_name in results:
        tasks.append(task)
        project_names[task.id] = project_name
    
    # Get assignees for all tasks
    task_ids = [t.id for t in tasks]
    assocs = (await db.execute(
        select(Assignment.task_id, User.full_name, User.email)
        .join(User, Assignment.assignee_id == User.id)
        .where(Assignment.task_id.in_(task_ids) if task_ids else False)
    )).all()
    
    assignees_map: dict[int, list[str]] = {}
    for task_id_val, full_name, email in assocs:
        label = (full_name or '').strip() or email
        assignees_map.setdefault(task_id_val, []).append(label)
    
    # Get all users and projects for filters
    users = (await db.execute(
        select(User)
        .where(User.workspace_id == user.workspace_id, User.is_active == True)
        .order_by(User.full_name, User.email)
    )).scalars().all()
    
    # Get projects user has access to
    if user.is_admin:
        projects = (await db.execute(
            select(Project)
            .where(Project.workspace_id == user.workspace_id)
            .order_by(Project.name)
        )).scalars().all()
    else:
        from app.models.project_member import ProjectMember
        projects = (await db.execute(
            select(Project)
            .join(ProjectMember, Project.id == ProjectMember.project_id)
            .where(
                ProjectMember.user_id == user_id,
                Project.workspace_id == user.workspace_id
            )
            .order_by(Project.name)
        )).scalars().all()
    
    return templates.TemplateResponse('tasks/list.html', {
        'request': request,
        'user': user,
        'tasks': tasks,
        'project_names': project_names,
        'assignees_map': assignees_map,
        'users': users,
        'projects': projects,
        'selected': {
            'tab': tab or 'active',
            'status': status,
            'priority': priority,
            'assignee_id': assignee_id,
            'project_id': project_id,
        },
        'today': date.today(),
    })


@router.get('/tasks/{task_id}')
async def web_task_detail(request: Request, task_id: int, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Get task with project check
    stmt = select(Task).join(Project, Task.project_id == Project.id).where(Task.id == task_id, Project.workspace_id == user.workspace_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    
    # Get project
    project = (await db.execute(select(Project).where(Project.id == task.project_id))).scalar_one_or_none()
    
    # Get assignments
    assignments = (await db.execute(
        select(User)
        .join(Assignment, User.id == Assignment.assignee_id)
        .where(Assignment.task_id == task_id)
    )).scalars().all()
    
    # Get comments with authors
    comments = (await db.execute(
        select(Comment, User)
        .join(User, Comment.author_id == User.id)
        .where(Comment.task_id == task_id)
        .order_by(Comment.created_at.desc())
    )).all()
    
    # Get attachments for all comments
    comment_ids = [c[0].id for c in comments]
    attachments_by_comment = {}
    if comment_ids:
        attachments = (await db.execute(
            select(CommentAttachment).where(CommentAttachment.comment_id.in_(comment_ids))
        )).scalars().all()
        for attachment in attachments:
            if attachment.comment_id not in attachments_by_comment:
                attachments_by_comment[attachment.comment_id] = []
            attachments_by_comment[attachment.comment_id].append(attachment)
    
    # Check if user can comment (admin or assignee)
    is_assignee = any(a.id == user_id for a in assignments)
    can_comment = user.is_admin or is_assignee
    
    # Get edit history
    history = (await db.execute(
        select(TaskHistory, User)
        .join(User, TaskHistory.editor_id == User.id)
        .where(TaskHistory.task_id == task_id)
        .order_by(TaskHistory.created_at.desc())
    )).all()
    
    # Get all active workspace users
    users = (await db.execute(select(User).where(User.workspace_id == user.workspace_id, User.is_active == True).order_by(User.full_name, User.email))).scalars().all()
    
    # Get subtasks ordered by their order field
    from app.models.subtask import Subtask
    subtasks = (await db.execute(
        select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.order)
    )).scalars().all()
    
    # Calculate subtask completion stats
    total_subtasks = len(subtasks)
    completed_subtasks = sum(1 for st in subtasks if st.is_completed)
    completion_percentage = int((completed_subtasks / total_subtasks) * 100) if total_subtasks > 0 else 0
    
    return templates.TemplateResponse('tasks/detail.html', {
        'request': request,
        'task': task,
        'project': project,
        'assignments': assignments,
        'comments': comments,
        'attachments_by_comment': attachments_by_comment,
        'can_comment': can_comment,
        'history': history,
        'users': users,
        'user': user,
        'subtasks': subtasks,
        'total_subtasks': total_subtasks,
        'completed_subtasks': completed_subtasks,
        'completion_percentage': completion_percentage,
        'TaskStatus': TaskStatus,
        'TaskPriority': TaskPriority
    })


@router.post('/tasks/{task_id}/update')
async def web_task_update(
    request: Request,
    task_id: int,
    title: str = Form(...),
    description: Optional[str] = Form(None),
    status: str = Form(...),
    priority: str = Form(...),
    start_date_value: Optional[str] = Form(None),
    start_time_value: Optional[str] = Form(None),
    due_date_value: Optional[str] = Form(None),
    due_time_value: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Get task
    stmt = select(Task).join(Project, Task.project_id == Project.id).where(Task.id == task_id, Project.workspace_id == user.workspace_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    
    # Check if task is archived - only admins can edit archived tasks
    if task.is_archived and not user.is_admin:
        raise HTTPException(status_code=403, detail='This task is archived. Only admins can modify it.')
    
    # Check permission: Admin OR assigned to this task
    if not user.is_admin:
        from app.models.assignment import Assignment
        assignment = (await db.execute(
            select(Assignment).where(
                Assignment.task_id == task_id,
                Assignment.assignee_id == user_id
            )
        )).scalar_one_or_none()
        if not assignment:
            raise HTTPException(status_code=403, detail='You can only edit tasks assigned to you')
    
    # Track changes
    from datetime import date, time
    changes = []
    
    if task.title != title:
        changes.append(('title', task.title, title))
        task.title = title
    
    if task.description != description:
        changes.append(('description', task.description or '', description or ''))
        task.description = description
    
    new_status = TaskStatus(status)
    if task.status != new_status:
        changes.append(('status', task.status.value, new_status.value))
        task.status = new_status
    
    new_priority = TaskPriority(priority)
    if task.priority != new_priority:
        changes.append(('priority', task.priority.value, new_priority.value))
        task.priority = new_priority
    
    new_start_date = date.fromisoformat(start_date_value) if start_date_value else None
    if task.start_date != new_start_date:
        changes.append(('start_date', str(task.start_date) if task.start_date else '', str(new_start_date) if new_start_date else ''))
        task.start_date = new_start_date
    
    new_start_time = time.fromisoformat(start_time_value) if start_time_value else None
    if task.start_time != new_start_time:
        changes.append(('start_time', str(task.start_time) if task.start_time else '', str(new_start_time) if new_start_time else ''))
        task.start_time = new_start_time
    
    new_due_date = date.fromisoformat(due_date_value) if due_date_value else None
    if task.due_date != new_due_date:
        changes.append(('due_date', str(task.due_date) if task.due_date else '', str(new_due_date) if new_due_date else ''))
        task.due_date = new_due_date
    
    new_due_time = time.fromisoformat(due_time_value) if due_time_value else None
    if task.due_time != new_due_time:
        changes.append(('due_time', str(task.due_time) if task.due_time else '', str(new_due_time) if new_due_time else ''))
        task.due_time = new_due_time
    
    # Save history
    for field, old_value, new_value in changes:
        history_entry = TaskHistory(
            task_id=task_id,
            editor_id=user_id,
            field=field,
            old_value=old_value,
            new_value=new_value
        )
        db.add(history_entry)
    
    await db.commit()
    return RedirectResponse(f'/web/tasks/{task_id}', status_code=303)


@router.post('/tasks/{task_id}/subtasks')
async def web_task_add_subtasks(
    request: Request,
    task_id: int,
    subtasks: str = Form(...),  # Newline-separated list of subtask titles
    db: AsyncSession = Depends(get_session)
):
    """Add multiple subtasks to a task at once."""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Get task and verify access
    stmt = select(Task).join(Project, Task.project_id == Project.id).where(
        Task.id == task_id, 
        Project.workspace_id == user.workspace_id
    )
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    
    # Check if task is archived
    if task.is_archived and not user.is_admin:
        raise HTTPException(status_code=403, detail='Cannot add subtasks to archived tasks')
    
    # Check permission: Admin OR assigned to this task
    if not user.is_admin:
        from app.models.assignment import Assignment
        assignment = (await db.execute(
            select(Assignment).where(
                Assignment.task_id == task_id,
                Assignment.assignee_id == user_id
            )
        )).scalar_one_or_none()
        if not assignment:
            raise HTTPException(status_code=403, detail='You can only add subtasks to tasks assigned to you')
    
    # Get current max order
    from app.models.subtask import Subtask
    max_order_result = await db.execute(
        select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.order.desc()).limit(1)
    )
    max_order_subtask = max_order_result.scalar_one_or_none()
    current_order = max_order_subtask.order if max_order_subtask else -1
    
    # Parse and create subtasks
    subtask_titles = [title.strip() for title in subtasks.split('\n') if title.strip()]
    
    for title in subtask_titles:
        current_order += 1
        new_subtask = Subtask(
            task_id=task_id,
            title=title,
            is_completed=False,
            order=current_order
        )
        db.add(new_subtask)
    
    await db.commit()
    return RedirectResponse(f'/web/tasks/{task_id}', status_code=303)


@router.post('/tasks/{task_id}/subtasks/{subtask_id}/toggle')
async def web_task_toggle_subtask(
    request: Request,
    task_id: int,
    subtask_id: int,
    db: AsyncSession = Depends(get_session)
):
    """Toggle a subtask's completion status."""
    user_id = request.session.get('user_id')
    if not user_id:
        return JSONResponse({'error': 'Not authenticated'}, status_code=401)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        return JSONResponse({'error': 'User not found'}, status_code=401)
    
    # Get subtask and verify it belongs to the task
    from app.models.subtask import Subtask
    subtask = (await db.execute(
        select(Subtask).where(Subtask.id == subtask_id, Subtask.task_id == task_id)
    )).scalar_one_or_none()
    
    if not subtask:
        return JSONResponse({'error': 'Subtask not found'}, status_code=404)
    
    # Get task and verify access
    stmt = select(Task).join(Project, Task.project_id == Project.id).where(
        Task.id == task_id,
        Project.workspace_id == user.workspace_id
    )
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        return JSONResponse({'error': 'Task not found'}, status_code=404)
    
    # Check if task is archived
    if task.is_archived and not user.is_admin:
        return JSONResponse({'error': 'Cannot modify subtasks in archived tasks'}, status_code=403)
    
    # Check permission: Admin OR assigned to this task
    if not user.is_admin:
        from app.models.assignment import Assignment
        assignment = (await db.execute(
            select(Assignment).where(
                Assignment.task_id == task_id,
                Assignment.assignee_id == user_id
            )
        )).scalar_one_or_none()
        if not assignment:
            return JSONResponse({'error': 'Permission denied'}, status_code=403)
    
    # Toggle completion
    from datetime import datetime
    subtask.is_completed = not subtask.is_completed
    subtask.completed_at = datetime.utcnow() if subtask.is_completed else None
    
    await db.commit()
    
    # Calculate completion percentage
    all_subtasks = (await db.execute(
        select(Subtask).where(Subtask.task_id == task_id)
    )).scalars().all()
    
    total = len(all_subtasks)
    completed = sum(1 for st in all_subtasks if st.is_completed)
    percentage = int((completed / total) * 100) if total > 0 else 0
    
    return JSONResponse({
        'success': True,
        'is_completed': subtask.is_completed,
        'completed_at': subtask.completed_at.isoformat() if subtask.completed_at else None,
        'total_subtasks': total,
        'completed_subtasks': completed,
        'completion_percentage': percentage
    })


@router.delete('/tasks/{task_id}/subtasks/{subtask_id}')
async def web_task_delete_subtask(
    request: Request,
    task_id: int,
    subtask_id: int,
    db: AsyncSession = Depends(get_session)
):
    """Delete a subtask."""
    user_id = request.session.get('user_id')
    if not user_id:
        return JSONResponse({'error': 'Not authenticated'}, status_code=401)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        return JSONResponse({'error': 'User not found'}, status_code=401)
    
    # Get subtask and verify it belongs to the task
    from app.models.subtask import Subtask
    subtask = (await db.execute(
        select(Subtask).where(Subtask.id == subtask_id, Subtask.task_id == task_id)
    )).scalar_one_or_none()
    
    if not subtask:
        return JSONResponse({'error': 'Subtask not found'}, status_code=404)
    
    # Get task and verify access
    stmt = select(Task).join(Project, Task.project_id == Project.id).where(
        Task.id == task_id,
        Project.workspace_id == user.workspace_id
    )
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        return JSONResponse({'error': 'Task not found'}, status_code=404)
    
    # Check if task is archived
    if task.is_archived and not user.is_admin:
        return JSONResponse({'error': 'Cannot delete subtasks from archived tasks'}, status_code=403)
    
    # Check permission: Admin OR assigned to this task
    if not user.is_admin:
        from app.models.assignment import Assignment
        assignment = (await db.execute(
            select(Assignment).where(
                Assignment.task_id == task_id,
                Assignment.assignee_id == user_id
            )
        )).scalar_one_or_none()
        if not assignment:
            return JSONResponse({'error': 'Permission denied'}, status_code=403)
    
    await db.delete(subtask)
    await db.commit()
    
    return JSONResponse({'success': True})


@router.post('/tasks/{task_id}/comment')
async def web_task_add_comment(
    request: Request,
    task_id: int,
    content: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Verify task exists and belongs to workspace
    stmt = select(Task).join(Project, Task.project_id == Project.id).where(Task.id == task_id, Project.workspace_id == user.workspace_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    
    # Check if task is archived - only admins can comment on archived tasks
    if task.is_archived and not user.is_admin:
        raise HTTPException(status_code=403, detail='This task is archived. Only admins can add comments.')
    
    # Check permission: only admin or assignees can comment
    is_assignee = (await db.execute(
        select(Assignment).where(Assignment.task_id == task_id, Assignment.assignee_id == user_id)
    )).scalar_one_or_none() is not None
    
    if not user.is_admin and not is_assignee:
        raise HTTPException(status_code=403, detail='Only assigned users and admins can comment on this task')
    
    comment = Comment(task_id=task_id, author_id=user_id, content=content)
    db.add(comment)
    await db.flush()  # Get comment.id for attachments
    
    # Notify all assignees except the commenter
    assignees_stmt = (
        select(User)
        .join(Assignment, User.id == Assignment.assignee_id)
        .where(Assignment.task_id == task_id, User.id != user_id)
    )
    assignees = (await db.execute(assignees_stmt)).scalars().all()
    
    commenter_name = user.full_name or user.username
    for assignee in assignees:
        notification = Notification(
            user_id=assignee.id,
            type='comment',
            message=f'{commenter_name} commented on task: {task.title}',
            url=f'/web/tasks/{task_id}'
        )
        db.add(notification)
    
    # Handle file attachments
    if files:
        # Create uploads directory if it doesn't exist
        upload_dir = BASE_DIR / 'uploads' / 'comments'
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        for file in files:
            if file.filename:  # Only process if file was actually uploaded
                # Read file content
                content = await file.read()
                
                # Validate file size (max 10MB)
                if len(content) > 10 * 1024 * 1024:
                    raise HTTPException(status_code=400, detail=f'File {file.filename} is too large. Maximum size is 10MB.')
                
                # Generate unique filename
                file_extension = os.path.splitext(file.filename)[1]
                unique_filename = f"{uuid.uuid4()}{file_extension}"
                file_path = upload_dir / unique_filename
                
                # Save file to disk
                with open(file_path, 'wb') as f:
                    f.write(content)
                
                # Create attachment record
                attachment = CommentAttachment(
                    comment_id=comment.id,
                    filename=file.filename,
                    file_path=str(file_path),
                    file_size=len(content),
                    content_type=file.content_type or 'application/octet-stream',
                    uploaded_by_id=user_id
                )
                db.add(attachment)
    
    await db.commit()
    return RedirectResponse(f'/web/tasks/{task_id}', status_code=303)


@router.get('/attachments/{attachment_id}/preview')
async def preview_comment_attachment(
    request: Request,
    attachment_id: int,
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Get attachment with permission check
    attachment = (await db.execute(
        select(CommentAttachment)
        .join(Comment, CommentAttachment.comment_id == Comment.id)
        .join(Task, Comment.task_id == Task.id)
        .join(Project, Task.project_id == Project.id)
        .where(
            CommentAttachment.id == attachment_id,
            Project.workspace_id == user.workspace_id
        )
    )).scalar_one_or_none()
    
    if not attachment:
        raise HTTPException(status_code=404, detail='Attachment not found')
    
    file_path = Path(attachment.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail='File not found on disk')
    
    # Serve file inline for preview (not as download)
    from fastapi.responses import Response
    with open(file_path, 'rb') as f:
        content = f.read()
    
    return Response(
        content=content,
        media_type=attachment.content_type,
        headers={
            'Content-Disposition': f'inline; filename="{attachment.filename}"'
        }
    )


@router.get('/attachments/{attachment_id}/download')
async def download_comment_attachment(
    request: Request,
    attachment_id: int,
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Get attachment with permission check
    attachment = (await db.execute(
        select(CommentAttachment)
        .join(Comment, CommentAttachment.comment_id == Comment.id)
        .join(Task, Comment.task_id == Task.id)
        .join(Project, Task.project_id == Project.id)
        .where(
            CommentAttachment.id == attachment_id,
            Project.workspace_id == user.workspace_id
        )
    )).scalar_one_or_none()
    
    if not attachment:
        raise HTTPException(status_code=404, detail='Attachment not found')
    
    file_path = Path(attachment.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail='File not found on disk')
    
    return FileResponse(
        path=str(file_path),
        filename=attachment.filename,
        media_type=attachment.content_type
    )


@router.post('/tasks/{task_id}/status')
async def web_task_update_status(request: Request, task_id: int, status_value: str = Form(...), db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    # Ensure task belongs to user's workspace
    stmt = select(Task).join(Project, Task.project_id == Project.id).where(Task.id == task_id, Project.workspace_id == user.workspace_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    
    # Check permission: Admin OR assigned to this task
    if not user.is_admin:
        from app.models.assignment import Assignment
        assignment = (await db.execute(
            select(Assignment).where(
                Assignment.task_id == task_id,
                Assignment.assignee_id == user_id
            )
        )).scalar_one_or_none()
        if not assignment:
            raise HTTPException(status_code=403, detail='You can only update tasks assigned to you')
    
    if status_value not in [s.value for s in TaskStatus]:
        raise HTTPException(status_code=400, detail='Invalid status')
    
    # Track change
    old_status = task.status.value
    task.status = TaskStatus(status_value)
    
    # Auto-archive when status changed to 'done'
    if status_value == 'done' and not task.is_archived:
        task.is_archived = True
        task.archived_at = datetime.utcnow()
    
    # Save history
    history_entry = TaskHistory(
        task_id=task_id,
        editor_id=user_id,
        field='status',
        old_value=old_status,
        new_value=status_value
    )
    db.add(history_entry)
    
    await db.commit()
    return RedirectResponse(f'/web/projects/{task.project_id}', status_code=303)


@router.post('/tasks/{task_id}/assign')
async def web_task_assign(request: Request, task_id: int, assignee_id: int = Form(...), db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Only admins can assign tasks to users
    if not user.is_admin:
        raise HTTPException(status_code=403, detail='Only admins can assign tasks')
    
    # Ensure task belongs to user's workspace
    stmt = select(Task).join(Project, Task.project_id == Project.id).where(Task.id == task_id, Project.workspace_id == user.workspace_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    # Verify assignee is in same workspace and is active
    assignee = (await db.execute(select(User).where(User.id == assignee_id, User.workspace_id == user.workspace_id, User.is_active == True))).scalar_one_or_none()
    if not assignee:
        raise HTTPException(status_code=400, detail='Invalid assignee or user is inactive')
    # Check if already assigned
    existing = (await db.execute(select(Assignment).where(Assignment.task_id == task_id, Assignment.assignee_id == assignee_id))).scalar_one_or_none()
    if not existing:
        assignment = Assignment(task_id=task_id, assignee_id=assignee_id, assigner_id=user_id)
        db.add(assignment)
        
        # Create notification for the assignee (don't notify if assigning to self)
        if assignee_id != user_id:
            assigner = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            assigner_name = assigner.full_name or assigner.username if assigner else "Someone"
            
            notification = Notification(
                user_id=assignee_id,
                type='task_assigned',
                message=f'{assigner_name} assigned you to task: {task.title}',
                url=f'/web/tasks/{task_id}'
            )
            db.add(notification)
        
        await db.commit()
    return RedirectResponse(f'/web/projects/{task.project_id}', status_code=303)


@router.post('/tasks/{task_id}/unassign')
async def web_task_unassign(request: Request, task_id: int, assignee_id: int = Form(...), db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Only admins can unassign tasks
    if not user.is_admin:
        raise HTTPException(status_code=403, detail='Only admins can unassign tasks')
    
    # Ensure task belongs to user's workspace
    stmt = select(Task).join(Project, Task.project_id == Project.id).where(Task.id == task_id, Project.workspace_id == user.workspace_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    # Delete assignment
    assignment = (await db.execute(select(Assignment).where(Assignment.task_id == task_id, Assignment.assignee_id == assignee_id))).scalar_one_or_none()
    if assignment:
        await db.delete(assignment)
        await db.commit()
    return RedirectResponse(f'/web/projects/{task.project_id}', status_code=303)


@router.post('/tasks/{task_id}/delete')
async def web_task_delete(request: Request, task_id: int, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Only admins can delete tasks
    if not user.is_admin:
        raise HTTPException(status_code=403, detail='Only admins can delete tasks')
    
    # Ensure task belongs to user's workspace
    stmt = select(Task).join(Project, Task.project_id == Project.id).where(Task.id == task_id, Project.workspace_id == user.workspace_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    
    project_id = task.project_id
    
    # Delete the task (cascade will handle assignments, comments, etc.)
    await db.delete(task)
    await db.commit()
    
    return RedirectResponse(f'/web/projects/{project_id}', status_code=303)


@router.post('/tasks/{task_id}/reopen')
async def web_task_reopen(request: Request, task_id: int, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Only admins can reopen tasks
    if not user.is_admin:
        raise HTTPException(status_code=403, detail='Only admins can reopen tasks')
    
    # Ensure task belongs to user's workspace
    stmt = select(Task).join(Project, Task.project_id == Project.id).where(Task.id == task_id, Project.workspace_id == user.workspace_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    
    # Reopen the task
    task.is_archived = False
    task.archived_at = None
    # If status is done, set it back to in_progress
    if task.status == TaskStatus.done:
        task.status = TaskStatus.in_progress
    
    await db.commit()
    
    return RedirectResponse(f'/web/tasks/{task_id}', status_code=303)


# Meetings
@router.get('/meetings')
async def web_meetings_list(request: Request, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Get all meetings where user is an attendee or organizer
    stmt = (
        select(Meeting)
        .join(MeetingAttendee, Meeting.id == MeetingAttendee.meeting_id)
        .where(MeetingAttendee.user_id == user_id)
        .order_by(Meeting.start_time.desc())
    )
    result = await db.execute(stmt)
    meetings = result.scalars().all()
    
    # Get all active workspace users for the create form
    users = (await db.execute(
        select(User)
        .where(User.workspace_id == user.workspace_id, User.is_active == True)
        .order_by(User.full_name, User.email)
    )).scalars().all()
    
    return templates.TemplateResponse('meetings/list.html', {
        'request': request,
        'meetings': meetings,
        'users': users,
        'user': user
    })


@router.post('/meetings/create')
async def web_meeting_create(
    request: Request,
    title: str = Form(...),
    start_time_str: str = Form(..., alias='start_time'),
    end_time_str: str = Form(..., alias='end_time'),
    platform: str = Form(...),
    meeting_url: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Parse datetime strings
    from datetime import datetime
    try:
        start_datetime = datetime.fromisoformat(start_time_str)
        end_datetime = datetime.fromisoformat(end_time_str)
        
        # Extract date and time components
        meeting_date = start_datetime.date()
        meeting_time = start_datetime.time()
        
        # Calculate duration in minutes
        duration = int((end_datetime - start_datetime).total_seconds() / 60)
        if duration <= 0:
            # Return user to form with error message instead of HTTP exception
            from fastapi.responses import HTMLResponse
            from fastapi.templating import Jinja2Templates
            templates = Jinja2Templates(directory="app/templates")
            
            # Get users for the form
            users_result = await db.execute(
                select(User).where(User.workspace_id == user.workspace_id).order_by(User.full_name)
            )
            users = users_result.scalars().all()
            
            return templates.TemplateResponse("meetings/list.html", {
                "request": request,
                "user": user,
                "meetings": [],
                "users": users,
                "error": "End time must be after start time. Please select a later end time."
            })
            
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f'Invalid datetime format: {str(e)}')
    
    # Create meeting
    meeting = Meeting(
        title=title,
        description=description,
        date=meeting_date,
        start_time=meeting_time,
        duration_minutes=duration,
        platform=MeetingPlatform(platform),
        url=meeting_url,
        organizer_id=user_id,
        workspace_id=user.workspace_id
    )
    db.add(meeting)
    await db.flush()  # Get the meeting ID
    
    # Add organizer as attendee
    attendee = MeetingAttendee(meeting_id=meeting.id, user_id=user_id)
    db.add(attendee)
    
    # Get attendee IDs from form (if provided)
    form_data = await request.form()
    attendee_ids = form_data.getlist('attendee_ids')
    for attendee_id in attendee_ids:
        if int(attendee_id) != user_id:  # Don't duplicate organizer
            attendee = MeetingAttendee(meeting_id=meeting.id, user_id=int(attendee_id))
            db.add(attendee)
    
    await db.commit()
    return RedirectResponse('/web/meetings', status_code=303)


@router.post('/meetings/{meeting_id}/cancel')
async def web_meeting_cancel(
    request: Request,
    meeting_id: int,
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Get the meeting
    meeting = (await db.execute(select(Meeting).where(Meeting.id == meeting_id))).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail='Meeting not found')
    
    # Check if user has permission (organizer or admin)
    if meeting.organizer_id != user_id and not user.is_admin:
        raise HTTPException(status_code=403, detail='Not authorized to cancel this meeting')
    
    # Check if already cancelled
    if meeting.is_cancelled:
        return RedirectResponse('/web/meetings', status_code=303)
    
    # Cancel the meeting
    from datetime import datetime
    meeting.is_cancelled = True
    meeting.cancelled_at = datetime.utcnow()
    meeting.cancelled_by = user_id
    
    await db.commit()
    return RedirectResponse('/web/meetings', status_code=303)


@router.get('/meetings/{meeting_id}/details')
async def web_meeting_details(
    request: Request,
    meeting_id: int,
    db: AsyncSession = Depends(get_session)
):
    """Get meeting details for display in modal"""
    user_id = request.session.get('user_id')
    if not user_id:
        return JSONResponse({'error': 'Not authenticated'}, status_code=401)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        return JSONResponse({'error': 'User not found'}, status_code=401)
    
    # Get the meeting
    meeting = (await db.execute(select(Meeting).where(Meeting.id == meeting_id))).scalar_one_or_none()
    if not meeting:
        return JSONResponse({'error': 'Meeting not found'}, status_code=404)
    
    # Check if user has access to this meeting (is attendee, organizer, or admin)
    is_organizer = meeting.organizer_id == user_id
    attendee = (await db.execute(
        select(MeetingAttendee).where(
            MeetingAttendee.meeting_id == meeting_id,
            MeetingAttendee.user_id == user_id
        )
    )).scalar_one_or_none()
    
    if not (is_organizer or attendee or user.is_admin):
        return JSONResponse({'error': 'Not authorized to view this meeting'}, status_code=403)
    
    # Get organizer details
    organizer = (await db.execute(select(User).where(User.id == meeting.organizer_id))).scalar_one_or_none()
    
    # Get all attendees
    attendees_data = (await db.execute(
        select(MeetingAttendee, User)
        .join(User, MeetingAttendee.user_id == User.id)
        .where(MeetingAttendee.meeting_id == meeting_id)
    )).all()
    
    # Get cancelled by user if meeting is cancelled
    cancelled_by_user = None
    if meeting.is_cancelled and meeting.cancelled_by:
        cancelled_by_user = (await db.execute(
            select(User).where(User.id == meeting.cancelled_by)
        )).scalar_one_or_none()
    
    return JSONResponse({
        'id': meeting.id,
        'title': meeting.title,
        'description': meeting.description,
        'date': meeting.date.strftime('%d/%m/%Y'),
        'date_formatted': meeting.date.strftime('%d/%m/%Y'),
        'start_time': meeting.start_time.strftime('%I:%M %p'),
        'duration_minutes': meeting.duration_minutes,
        'platform': meeting.platform.value,
        'platform_display': meeting.platform.value.replace('_', ' ').title(),
        'url': meeting.url,
        'organizer': {
            'id': organizer.id if organizer else None,
            'name': organizer.full_name if organizer else 'Unknown',
            'email': organizer.email if organizer else ''
        },
        'attendees': [
            {
                'id': user_obj.id,
                'name': user_obj.full_name,
                'email': user_obj.email,
                'status': attendee_obj.status or 'invited'
            }
            for attendee_obj, user_obj in attendees_data
        ],
        'is_cancelled': meeting.is_cancelled,
        'cancelled_at': meeting.cancelled_at.strftime('%d/%m/%Y at %I:%M %p') if meeting.cancelled_at else None,
        'cancelled_by': cancelled_by_user.full_name if cancelled_by_user else None,
        'is_organizer': is_organizer,
        'is_admin': user.is_admin
    })


# Calendar
@router.get('/calendar')
async def web_calendar(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
    day: Optional[int] = None,
    view: str = 'month',
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Default to current date if not specified
    today = date.today()
    year = year or today.year
    month = month or today.month
    day = day or today.day
    
    # Calculate prev/next month for navigation
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year
    
    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year
    
    # Determine date range based on view
    if view == 'day':
        current_date = date(year, month, day)
        first_day = current_date
        last_day = current_date
        weeks = []
    elif view == 'week':
        current_date = date(year, month, day)
        # Get Monday of current week
        start_of_week = current_date - timedelta(days=current_date.weekday())
        first_day = start_of_week
        last_day = start_of_week + timedelta(days=6)
        weeks = [[start_of_week + timedelta(days=i) for i in range(7)]]
    else:  # month view
        current_date = date(year, month, day)
        # Build calendar weeks with date objects
        cal = pycalendar.Calendar(firstweekday=0)  # Monday first
        weeks = []
        for week in cal.monthdatescalendar(year, month):
            weeks.append(week)
        # Get all tasks with due dates for display (include adjacent months shown in calendar)
        first_day = weeks[0][0]
        last_day = weeks[-1][-1]
    
    # Admin sees all tasks, regular users see only their assigned tasks
    # Fetch tasks that overlap with the calendar view period (either by start_date or due_date)
    if user.is_admin:
        stmt = (
            select(Task)
            .join(Project, Task.project_id == Project.id)
            .where(
                Project.workspace_id == user.workspace_id,
                # Task has at least a due_date or start_date
                (Task.due_date.isnot(None)) | (Task.start_date.isnot(None)),
                # Task overlaps with calendar period
                (
                    # Tasks with both start and due dates - check if they overlap calendar period
                    ((Task.start_date.isnot(None)) & (Task.due_date.isnot(None)) & 
                     (Task.start_date <= last_day) & (Task.due_date >= first_day)) |
                    # Tasks with only due_date - check if in period
                    ((Task.start_date.is_(None)) & (Task.due_date.isnot(None)) & 
                     (Task.due_date >= first_day) & (Task.due_date <= last_day)) |
                    # Tasks with only start_date - check if in period
                    ((Task.start_date.isnot(None)) & (Task.due_date.is_(None)) & 
                     (Task.start_date >= first_day) & (Task.start_date <= last_day))
                )
            )
            .order_by(Task.start_date, Task.due_date, Task.due_time)
        )
    else:
        stmt = (
            select(Task)
            .join(Project, Task.project_id == Project.id)
            .join(Assignment, Task.id == Assignment.task_id)
            .where(
                Project.workspace_id == user.workspace_id,
                Assignment.assignee_id == user.id,
                # Task has at least a due_date or start_date
                (Task.due_date.isnot(None)) | (Task.start_date.isnot(None)),
                # Task overlaps with calendar period
                (
                    # Tasks with both start and due dates - check if they overlap calendar period
                    ((Task.start_date.isnot(None)) & (Task.due_date.isnot(None)) & 
                     (Task.start_date <= last_day) & (Task.due_date >= first_day)) |
                    # Tasks with only due_date - check if in period
                    ((Task.start_date.is_(None)) & (Task.due_date.isnot(None)) & 
                     (Task.due_date >= first_day) & (Task.due_date <= last_day)) |
                    # Tasks with only start_date - check if in period
                    ((Task.start_date.isnot(None)) & (Task.due_date.is_(None)) & 
                     (Task.start_date >= first_day) & (Task.start_date <= last_day))
                )
            )
            .order_by(Task.start_date, Task.due_date, Task.due_time)
        )
    tasks = (await db.execute(stmt)).scalars().all()
    
    # Fetch projects with date ranges for calendar display
    # Admin sees all projects, regular users see projects they're assigned to (via tasks or project_member)
    from app.models.project_member import ProjectMember
    
    if user.is_admin:
        projects_stmt = (
            select(Project)
            .where(
                Project.workspace_id == user.workspace_id,
                Project.start_date.isnot(None),
                Project.due_date.isnot(None),
                Project.is_archived == False,
                # Project overlaps with calendar view period
                Project.start_date <= last_day,
                Project.due_date >= first_day
            )
            .order_by(Project.start_date)
        )
    else:
        # Get projects where user is a member or has assigned tasks
        projects_stmt = (
            select(Project)
            .join(ProjectMember, Project.id == ProjectMember.project_id)
            .where(
                Project.workspace_id == user.workspace_id,
                ProjectMember.user_id == user.id,
                Project.start_date.isnot(None),
                Project.due_date.isnot(None),
                Project.is_archived == False,
                Project.start_date <= last_day,
                Project.due_date >= first_day
            )
            .distinct()
            .order_by(Project.start_date)
        )
    projects = (await db.execute(projects_stmt)).scalars().all()
    
    # Sort tasks by priority (critical, high, medium, low)
    priority_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    tasks = sorted(tasks, key=lambda t: (t.due_date, priority_order.get(t.priority.value, 4), t.due_time or time.max))
    
    # Get meetings for the calendar period
    # Admin sees all meetings, regular users see only meetings they're attending
    if user.is_admin:
        meetings_stmt = (
            select(Meeting)
            .where(
                Meeting.workspace_id == user.workspace_id,
                Meeting.date >= first_day,
                Meeting.date <= last_day
            )
            .order_by(Meeting.date, Meeting.start_time)
        )
    else:
        meetings_stmt = (
            select(Meeting)
            .join(MeetingAttendee, Meeting.id == MeetingAttendee.meeting_id)
            .where(
                Meeting.workspace_id == user.workspace_id,
                MeetingAttendee.user_id == user.id,
                Meeting.date >= first_day,
                Meeting.date <= last_day
            )
            .order_by(Meeting.date, Meeting.start_time)
        )
    meetings = (await db.execute(meetings_stmt)).scalars().all()
    
    # Get all workspace users for color legend (admin view)
    workspace_users = []
    if user.is_admin:
        workspace_users_stmt = (
            select(User)
            .where(
                User.workspace_id == user.workspace_id,
                User.is_active == True
            )
            .order_by(User.full_name)
        )
        workspace_users = (await db.execute(workspace_users_stmt)).scalars().all()
    
    # Build a map of task/project IDs to their assigned users for color coding
    task_users = {}
    for task in tasks:
        # Get all users assigned to this task
        task_assignments = (await db.execute(
            select(User)
            .join(Assignment, User.id == Assignment.assignee_id)
            .where(Assignment.task_id == task.id)
        )).scalars().all()
        if task_assignments:
            task_users[task.id] = list(task_assignments)  # Store all assigned users
    
    project_users = {}
    for project in projects:
        # Get project owner for color coding
        project_owner = (await db.execute(
            select(User).where(User.id == project.owner_id)
        )).scalar_one_or_none()
        if project_owner:
            project_users[project.id] = project_owner
    
    # Calculate navigation dates based on view
    if view == 'day':
        prev_date = current_date - timedelta(days=1)
        next_date = current_date + timedelta(days=1)
        prev_year, prev_month, prev_day = prev_date.year, prev_date.month, prev_date.day
        next_year, next_month, next_day = next_date.year, next_date.month, next_date.day
    elif view == 'week':
        prev_week_start = first_day - timedelta(days=7)
        next_week_start = first_day + timedelta(days=7)
        prev_year, prev_month, prev_day = prev_week_start.year, prev_week_start.month, prev_week_start.day
        next_year, next_month, next_day = next_week_start.year, next_week_start.month, next_week_start.day
    else:  # month
        prev_day = 1
        next_day = 1
    
    return templates.TemplateResponse('calendar/index.html', {
        'request': request,
        'user': user,
        'view': view,
        'year': year,
        'month': month,
        'day': day,
        'current_date': current_date,
        'first_day': first_day,
        'last_day': last_day,
        'weeks': weeks,
        'tasks': tasks,
        'meetings': meetings,
        'projects': projects,
        'task_users': task_users,
        'project_users': project_users,
        'workspace_users': workspace_users,
        'today': today,
        'prev_month': prev_month,
        'prev_year': prev_year,
        'prev_day': prev_day,
        'next_month': next_month,
        'next_year': next_year,
        'next_day': next_day,
        'TaskStatus': TaskStatus
    })


# ====================================
# Tickets System
# ====================================
@router.get('/tickets', response_class=HTMLResponse)
async def web_tickets_list(request: Request, db: AsyncSession = Depends(get_session)):
    """List all tickets with filters"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    from app.models.ticket import Ticket
    
    # Get filter parameters
    status_filter = request.query_params.get('status', 'all')
    priority_filter = request.query_params.get('priority', 'all')
    assigned_filter = request.query_params.get('assigned', 'all')
    project_filter = request.query_params.get('project', 'all')  # New: filter by project scope
    
    # Base query - exclude archived
    query = select(Ticket).where(
        Ticket.workspace_id == user.workspace_id,
        Ticket.is_archived == False
    )
    
    # Project-based visibility based on user permissions
    if not user.is_admin and not user.can_see_all_tickets:
        # Regular users: see only their project tickets and assigned tickets
        # Get projects where user is a member
        from app.models.project_member import ProjectMember
        user_projects_query = select(ProjectMember.project_id).where(
            ProjectMember.user_id == user_id
        )
        user_project_ids = (await db.execute(user_projects_query)).scalars().all()
        
        # User can see:
        # 1. Tickets assigned to them
        # 2. Tickets related to their projects
        # 3. Tickets with no project assignment (general tickets) if they can access main system
        query = query.where(
            (Ticket.assigned_to_id == user_id) |
            (Ticket.related_project_id.in_(user_project_ids)) |
            ((Ticket.related_project_id.is_(None)) & (Ticket.assigned_to_id == user_id))
        )
    
    # Project filter (for admins or filtering within allowed scope)
    if project_filter == 'main':
        # Show only tickets not related to any project (main ticket system)
        query = query.where(Ticket.related_project_id.is_(None))
    elif project_filter != 'all':
        try:
            project_id = int(project_filter)
            query = query.where(Ticket.related_project_id == project_id)
        except ValueError:
            pass
    
    # Apply other filters
    if status_filter != 'all':
        query = query.where(Ticket.status == status_filter)
    if priority_filter != 'all':
        query = query.where(Ticket.priority == priority_filter)
    if assigned_filter == 'me':
        query = query.where(Ticket.assigned_to_id == user_id)
    elif assigned_filter == 'unassigned':
        query = query.where(Ticket.assigned_to_id.is_(None))
    
    query = query.order_by(Ticket.created_at.desc())
    tickets = (await db.execute(query)).scalars().all()
    
    # Get user's projects for filter dropdown
    user_projects = []
    if not user.is_admin and not user.can_see_all_tickets:
        # Limited users: show only their assigned projects
        from app.models.project_member import ProjectMember
        from app.models.project import Project
        user_projects_query = select(Project).join(
            ProjectMember, Project.id == ProjectMember.project_id
        ).where(ProjectMember.user_id == user_id)
        user_projects = (await db.execute(user_projects_query)).scalars().all()
    else:
        # Admins and users with full ticket access see all projects
        from app.models.project import Project
        user_projects = (await db.execute(
            select(Project).where(Project.workspace_id == user.workspace_id)
        )).scalars().all()
    
    # Get all users for assignment dropdown
    users = (await db.execute(
        select(User).where(User.workspace_id == user.workspace_id)
    )).scalars().all()
    
    return templates.TemplateResponse('tickets/list.html', {
        'request': request,
        'user': user,
        'tickets': tickets,
        'users': users,
        'status_filter': status_filter,
        'priority_filter': priority_filter,
        'assigned_filter': assigned_filter,
        'project_filter': project_filter,
        'user_projects': user_projects
    })


@router.post('/tickets/create')
async def web_tickets_create(
    request: Request,
    subject: str = Form(...),
    description: Optional[str] = Form(None),
    priority: str = Form('medium'),
    category: str = Form('general'),
    assigned_to_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_session)
):
    """Create a new ticket"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    from app.models.ticket import Ticket, TicketHistory
    from datetime import datetime
    
    # Generate ticket number
    year = datetime.utcnow().year
    result = await db.execute(
        select(Ticket).where(Ticket.workspace_id == user.workspace_id)
    )
    ticket_count = len(result.scalars().all()) + 1
    ticket_number = f"TKT-{year}-{ticket_count:05d}"
    
    # Create ticket
    ticket = Ticket(
        ticket_number=ticket_number,
        subject=subject,
        description=description,
        priority=priority,
        category=category,
        assigned_to_id=assigned_to_id,
        created_by_id=user_id,
        workspace_id=user.workspace_id
    )
    db.add(ticket)
    await db.flush()
    
    # Create history entry
    history = TicketHistory(
        ticket_id=ticket.id,
        user_id=user_id,
        action='created',
        new_value=f'Ticket created with priority: {priority}'
    )
    db.add(history)
    
    # Create notification if assigned
    if assigned_to_id and assigned_to_id != user_id:
        notification = Notification(
            user_id=assigned_to_id,
            type='ticket',
            message=f'{user.full_name or user.username} assigned you ticket #{ticket_number}: {subject}',
            url=f'/web/tickets/{ticket.id}',
            related_id=ticket.id
        )
        db.add(notification)
    elif not assigned_to_id:
        # Notify all admins if ticket is not assigned
        admin_users = (await db.execute(
            select(User).where(User.workspace_id == user.workspace_id).where(User.is_admin == True)
        )).scalars().all()
        
        for admin in admin_users:
            if admin.id != user_id:  # Don't notify the creator if they're admin
                notification = Notification(
                    user_id=admin.id,
                    type='ticket',
                    message=f'{user.full_name or user.username} created unassigned ticket #{ticket_number}: {subject}',
                    url=f'/web/tickets/{ticket.id}',
                    related_id=ticket.id
                )
                db.add(notification)
    
    await db.commit()
    return RedirectResponse(f'/web/tickets/{ticket.id}', status_code=303)


# Guest ticket routes (must be before /tickets/{ticket_id} to avoid route conflict)
@router.get('/tickets/guest', response_class=HTMLResponse)
async def web_tickets_guest_form(request: Request):
    """Public guest ticket submission form (no login required)"""
    return templates.TemplateResponse('tickets/guest.html', {
        'request': request,
        'success': False
    })


@router.post('/tickets/guest')
async def web_tickets_guest_submit(
    request: Request,
    guest_name: str = Form(...),
    guest_surname: str = Form(...),
    guest_email: str = Form(...),
    guest_phone: str = Form(...),
    guest_company: str = Form(...),
    guest_office_number: Optional[str] = Form(None),
    guest_branch: Optional[str] = Form(None),
    subject: str = Form(...),
    description: str = Form(...),
    db: AsyncSession = Depends(get_session)
):
    """Handle guest ticket submission"""
    from app.models.ticket import Ticket, TicketHistory
    from app.models.email_settings import EmailSettings
    from datetime import datetime
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    try:
        # Use workspace 1 as default for guest tickets
        workspace_id = 1
        
        # Auto-set priority to medium for guest tickets
        priority = 'medium'
        
        # Generate ticket number
        year = datetime.utcnow().year
        result = await db.execute(
            select(Ticket).where(Ticket.workspace_id == workspace_id)
        )
        ticket_count = len(result.scalars().all()) + 1
        ticket_number = f"TKT-{year}-{ticket_count:05d}"
        
        # Create ticket
        ticket = Ticket(
            ticket_number=ticket_number,
            subject=subject,
            description=description,
            priority=priority,
            category='support',
            status='open',
            workspace_id=workspace_id,
            is_guest=True,
            guest_name=guest_name,
            guest_surname=guest_surname,
            guest_email=guest_email,
            guest_phone=guest_phone,
            guest_company=guest_company,
            guest_office_number=guest_office_number,
            guest_branch=guest_branch,
            created_by_id=None  # No user account
        )
        db.add(ticket)
        await db.flush()
        
        # Create history entry
        history = TicketHistory(
            ticket_id=ticket.id,
            user_id=None,
            action='created',
            new_value=f'Guest ticket created from {guest_email}'
        )
        db.add(history)
        
        # Notify all admins about new ticket
        from app.models.notification import Notification
        admin_users = (await db.execute(
            select(User).where(User.workspace_id == workspace_id).where(User.is_admin == True)
        )).scalars().all()
        
        for admin in admin_users:
            notification = Notification(
                user_id=admin.id,
                type='ticket',
                message=f'New guest ticket #{ticket_number}: {subject}',
                url=f'/web/tickets/{ticket.id}',
                related_id=ticket.id
            )
            db.add(notification)
        
        await db.commit()
        
        # Try to send confirmation email
        email_sent = False
        try:
            # Get email settings
            email_settings = (await db.execute(
                select(EmailSettings).where(EmailSettings.workspace_id == workspace_id)
            )).scalar_one_or_none()
            
            if email_settings and email_settings.auto_reply_enabled:
                # Prepare email
                subject_template = email_settings.confirmation_subject
                body_template = email_settings.confirmation_body
                
                # Replace variables
                email_subject = subject_template.format(
                    ticket_number=ticket_number,
                    subject=subject,
                    priority=priority
                )
                
                email_body = body_template.format(
                    guest_name=guest_name,
                    guest_surname=guest_surname,
                    ticket_number=ticket_number,
                    subject=subject,
                    priority=priority,
                    company_name=email_settings.company_name
                )
                
                # Send email
                import uuid
                message_id = f"<{ticket_number}.{uuid.uuid4()}@{email_settings.smtp_host}>"
                
                msg = MIMEMultipart()
                msg['From'] = f"{email_settings.smtp_from_name} <{email_settings.smtp_from_email}>"
                msg['To'] = guest_email
                msg['Subject'] = email_subject
                msg['Message-ID'] = message_id
                msg.attach(MIMEText(email_body, 'plain'))
                
                server = smtplib.SMTP(email_settings.smtp_host, email_settings.smtp_port)
                if email_settings.smtp_use_tls:
                    server.starttls()
                server.login(email_settings.smtp_username, email_settings.smtp_password)
                server.send_message(msg)
                server.quit()
                
                email_sent = True
                
                # Store the message ID so replies can be tracked
                from app.models.processed_mail import ProcessedMail
                processed = ProcessedMail(
                    message_id=message_id,
                    email_from=email_settings.smtp_from_email,
                    subject=email_subject,
                    ticket_id=ticket.id,
                    workspace_id=workspace_id,
                    processed_at=datetime.utcnow()
                )
                db.add(processed)
                await db.commit()
        except Exception as e:
            print(f"Failed to send confirmation email: {e}")
            # Continue even if email fails
        
        return templates.TemplateResponse('tickets/guest.html', {
            'request': request,
            'success': True,
            'ticket_number': ticket_number,
            'guest_email': guest_email,
            'email_sent': email_sent
        })
        
    except Exception as e:
        return templates.TemplateResponse('tickets/guest.html', {
            'request': request,
            'success': False,
            'error': f"Failed to create ticket: {str(e)}"
        })


# Public ticket tracking routes (no login required)
@router.get('/tickets/track', response_class=HTMLResponse)
async def web_tickets_track_form(request: Request):
    """Public ticket tracking form"""
    error_message = request.session.pop('error_message', None)
    return templates.TemplateResponse('tickets/track.html', {
        'request': request,
        'error_message': error_message
    })


@router.post('/tickets/track')
async def web_tickets_track_submit(
    request: Request,
    ticket_number: str = Form(...),
    email: str = Form(...),
    db: AsyncSession = Depends(get_session)
):
    """Verify ticket and email, then show tracking details"""
    from app.models.ticket import Ticket
    
    # Find ticket by number
    result = await db.execute(
        select(Ticket).where(Ticket.ticket_number == ticket_number.strip())
    )
    ticket = result.scalar_one_or_none()
    
    if not ticket:
        request.session['error_message'] = 'Ticket not found. Please check the ticket number and try again.'
        return RedirectResponse('/web/tickets/track', status_code=303)
    
    # Verify email matches (check both guest email and requester email if user account)
    email_lower = email.strip().lower()
    ticket_emails = []
    
    if ticket.guest_email:
        ticket_emails.append(ticket.guest_email.lower())
    
    # If ticket has a user, check their email too
    if ticket.created_by_id:
        user = (await db.execute(select(User).where(User.id == ticket.created_by_id))).scalar_one_or_none()
        if user and user.email:
            ticket_emails.append(user.email.lower())
    
    if email_lower not in ticket_emails:
        request.session['error_message'] = 'Email address does not match this ticket. Please use the email you submitted the ticket with.'
        return RedirectResponse('/web/tickets/track', status_code=303)
    
    # Redirect to tracking detail page
    return RedirectResponse(f'/web/tickets/track/{ticket_number}', status_code=303)


@router.get('/tickets/track/{ticket_number}', response_class=HTMLResponse)
async def web_tickets_track_detail(
    request: Request,
    ticket_number: str,
    db: AsyncSession = Depends(get_session)
):
    """Show ticket tracking details (must have verified via POST first or have session)"""
    from app.models.ticket import Ticket, TicketComment
    
    # Find ticket
    result = await db.execute(
        select(Ticket).where(Ticket.ticket_number == ticket_number)
    )
    ticket = result.scalar_one_or_none()
    
    if not ticket:
        request.session['error_message'] = 'Ticket not found.'
        return RedirectResponse('/web/tickets/track', status_code=303)
    
    # Get comments (only non-internal ones for public view)
    comments_result = await db.execute(
        select(TicketComment)
        .where(TicketComment.ticket_id == ticket.id)
        .where(TicketComment.is_internal == False)
        .order_by(TicketComment.created_at.asc())
    )
    comments = comments_result.scalars().all()
    
    # Load user info for comments and create list of dicts
    comments_with_users = []
    for comment in comments:
        user = None
        if comment.user_id:
            user = (await db.execute(select(User).where(User.id == comment.user_id))).scalar_one_or_none()
        
        comments_with_users.append({
            'comment': comment,
            'user': user
        })
    
    return templates.TemplateResponse('tickets/track_detail.html', {
        'request': request,
        'ticket': ticket,
        'comments': comments_with_users
    })


@router.get('/tickets/archived', response_class=HTMLResponse)
async def web_tickets_archived(request: Request, db: AsyncSession = Depends(get_session)):
    """View archived tickets"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    from app.models.ticket import Ticket
    
    # Get archived tickets
    tickets = (await db.execute(
        select(Ticket).where(
            Ticket.workspace_id == user.workspace_id,
            Ticket.is_archived == True
        ).order_by(Ticket.archived_at.desc())
    )).scalars().all()
    
    return templates.TemplateResponse('tickets/archived.html', {
        'request': request,
        'user': user,
        'tickets': tickets
    })


@router.get('/tickets/{ticket_id}', response_class=HTMLResponse)
async def web_tickets_detail(request: Request, ticket_id: int, db: AsyncSession = Depends(get_session)):
    """View ticket details with comments and history"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    from app.models.ticket import Ticket, TicketComment, TicketAttachment, TicketHistory
    
    # Get ticket
    ticket = (await db.execute(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.workspace_id == user.workspace_id
        )
    )).scalar_one_or_none()
    
    if not ticket:
        return RedirectResponse('/web/tickets', status_code=303)
    
    # Get creator
    creator = (await db.execute(select(User).where(User.id == ticket.created_by_id))).scalar_one_or_none()
    
    # Get assigned user
    assigned_user = None
    if ticket.assigned_to_id:
        assigned_user = (await db.execute(select(User).where(User.id == ticket.assigned_to_id))).scalar_one_or_none()
    
    # Get comments
    comments_result = await db.execute(
        select(TicketComment)
        .where(TicketComment.ticket_id == ticket_id)
        .order_by(TicketComment.created_at.asc())
    )
    comments = comments_result.scalars().all()
    
    # Get comment authors
    comment_authors = {}
    for comment in comments:
        # TicketComment uses `user_id` (nullable for guest comments)
        if comment.user_id not in comment_authors:
            author = (await db.execute(select(User).where(User.id == comment.user_id))).scalar_one_or_none() if comment.user_id else None
            comment_authors[comment.user_id] = author
    
    # Get attachments
    attachments = (await db.execute(
        select(TicketAttachment).where(TicketAttachment.ticket_id == ticket_id)
    )).scalars().all()
    
    # Get history
    history = (await db.execute(
        select(TicketHistory)
        .where(TicketHistory.ticket_id == ticket_id)
        .order_by(TicketHistory.created_at.desc())
    )).scalars().all()
    
    # Get all users for assignment
    users = (await db.execute(
        select(User).where(User.workspace_id == user.workspace_id)
    )).scalars().all()
    
    # Get related project if exists
    related_project = None
    if ticket.related_project_id:
        from app.models.project import Project
        related_project = (await db.execute(
            select(Project).where(Project.id == ticket.related_project_id)
        )).scalar_one_or_none()
    
    # Get closed_by user if exists
    closed_by_user = None
    if ticket.closed_by_id:
        closed_by_user = (await db.execute(
            select(User).where(User.id == ticket.closed_by_id)
        )).scalar_one_or_none()
    
    return templates.TemplateResponse('tickets/detail.html', {
        'request': request,
        'user': user,
        'ticket': ticket,
        'creator': creator,
        'assigned_user': assigned_user,
        'comments': comments,
        'comment_authors': comment_authors,
        'attachments': attachments,
        'history': history,
        'users': users,
        'related_project': related_project,
        'closed_by_user': closed_by_user
    })


@router.post('/tickets/{ticket_id}/comment')
async def web_tickets_add_comment(
    request: Request,
    ticket_id: int,
    content: str = Form(...),
    is_internal: bool = Form(False),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_session)
):
    """Add comment to ticket"""
    from fastapi import BackgroundTasks
    
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    from app.models.ticket import Ticket, TicketComment, TicketHistory
    
    # Verify ticket exists
    ticket = (await db.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
    if not ticket:
        return RedirectResponse('/web/tickets', status_code=303)
    
    # Check if ticket is closed
    if ticket.status == 'closed':
        # Get user info
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        
        # Send email notification about closed ticket
        if ticket.is_guest and ticket.guest_email:
            try:
                from app.models.email_settings import EmailSettings
                import smtplib
                from email.mime.text import MIMEText
                from email.mime.multipart import MIMEMultipart
                
                # Get email settings
                settings_result = await db.execute(
                    select(EmailSettings).where(EmailSettings.workspace_id == ticket.workspace_id)
                )
                email_settings = settings_result.scalar_one_or_none()
                
                if email_settings and email_settings.smtp_enabled:
                    # Create message
                    msg = MIMEMultipart('alternative')
                    msg['From'] = email_settings.smtp_username
                    msg['To'] = ticket.guest_email
                    msg['Subject'] = f"Ticket #{ticket.ticket_number} is Closed"
                    
                    body = f"""
This ticket has been marked as closed and cannot accept new comments.

Ticket: #{ticket.ticket_number}
Subject: {ticket.subject}

If you need further assistance, please:
- Contact support at {email_settings.smtp_username} to request reopening this ticket
- Or submit a new ticket at: https://kyotech.co.za/web/tickets/guest

Thank you.
"""
                    msg.attach(MIMEText(body, 'plain'))
                    
                    # Send email
                    if email_settings.smtp_use_ssl:
                        server = smtplib.SMTP_SSL(email_settings.smtp_host, email_settings.smtp_port)
                    else:
                        server = smtplib.SMTP(email_settings.smtp_host, email_settings.smtp_port)
                        if email_settings.smtp_use_tls:
                            server.starttls()
                    
                    server.login(email_settings.smtp_username, email_settings.smtp_password)
                    server.send_message(msg)
                    server.quit()
            except Exception as e:
                print(f"Error sending closed ticket notification: {e}")
        
        # Redirect with error message
        request.session['error_message'] = 'This ticket is closed and cannot accept new comments. Please contact support to reopen.'
        return RedirectResponse(f'/web/tickets/{ticket_id}', status_code=303)
    
    # Add comment
    comment = TicketComment(
        ticket_id=ticket_id,
        user_id=user_id,
        content=content,
        is_internal=is_internal
    )
    db.add(comment)
    
    # Update ticket timestamp
    from datetime import datetime
    ticket.updated_at = datetime.utcnow()
    
    # Add history
    history = TicketHistory(
        ticket_id=ticket_id,
        user_id=user_id,
        action='commented',
        new_value='Added a comment'
    )
    db.add(history)
    
    await db.commit()
    await db.refresh(comment)
    
    # Send email notification to client in background (if not internal comment)
    if not is_internal and ticket.guest_email:
        print(f"[DEBUG] Triggering email notification for ticket #{ticket.ticket_number} to {ticket.guest_email}")
        print(f"[DEBUG] is_internal={is_internal}, guest_email={ticket.guest_email}")
        # Send email in a separate thread
        import threading
        thread = threading.Thread(
            target=send_ticket_comment_email_threaded,
            args=(ticket.id, ticket.workspace_id, content, user_id)
        )
        thread.daemon = True
        thread.start()
    else:
        print(f"[DEBUG] NOT sending email: is_internal={is_internal}, guest_email={ticket.guest_email}")
    
    return RedirectResponse(f'/web/tickets/{ticket_id}', status_code=303)


def send_ticket_comment_email_threaded(ticket_id: int, workspace_id: int, content: str, user_id: int):
    """Send email in a separate thread (synchronous)"""
    import asyncio
    try:
        print(f"[EMAIL] Thread started for ticket #{ticket_id}")
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                send_ticket_comment_email_background(ticket_id, workspace_id, content, user_id)
            )
        finally:
            loop.close()
    except Exception as e:
        print(f"❌ Error in email thread: {e}")
        import traceback
        traceback.print_exc()


async def send_ticket_comment_email_background(ticket_id: int, workspace_id: int, content: str, user_id: int):
    """Send email notification in background with new DB session (non-blocking)"""
    try:
        print(f"[EMAIL] Background task started for ticket #{ticket_id}")
        
        # Create new database session for background task
        from app.core.database import async_session_maker
        async with async_session_maker() as db:
            # Reload ticket in new session
            ticket = (await db.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
            if not ticket:
                print(f"❌ Ticket {ticket_id} not found in background task")
                return
            
            print(f"[EMAIL] Ticket loaded: #{ticket.ticket_number}, guest_email={ticket.guest_email}")
            
            await send_ticket_comment_email(ticket, content, user_id, db)
    except Exception as e:
        print(f"❌ Error in background email task: {e}")
        import traceback
        traceback.print_exc()


async def send_ticket_comment_email(ticket: Ticket, content: str, user_id: int, db: AsyncSession):
    """Send email notification in background (non-blocking)"""
    try:
        print(f"[EMAIL] send_ticket_comment_email called for ticket #{ticket.ticket_number}")
        
        # Get user info
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        print(f"[EMAIL] User: {user.username if user else 'None'}")
        
        # Get email settings (always needed for SMTP connection)
        from app.models.email_settings import EmailSettings
        settings_result = await db.execute(
            select(EmailSettings).where(EmailSettings.workspace_id == ticket.workspace_id)
        )
        email_settings = settings_result.scalar_one_or_none()
        
        if not email_settings:
            print(f"❌ No email settings configured for workspace {ticket.workspace_id}")
            return
        
        print(f"[EMAIL] SMTP settings found: {email_settings.smtp_host}:{email_settings.smtp_port}")
        
        # Determine which email to send from
        from_email = email_settings.smtp_from_email
        from_name = email_settings.smtp_from_name or "Support Team"
        
        print(f"[EMAIL] Default from: {from_name} <{from_email}>")
        
        # Check if ticket is related to a project with support email
        if ticket.related_project_id:
            from app.models.project import Project
            project = (await db.execute(
                select(Project).where(Project.id == ticket.related_project_id)
            )).scalar_one_or_none()
            
            if project and project.support_email:
                from_email = project.support_email
                from_name = f"{project.name} Support"
                print(f"[EMAIL] Using project email: {from_name} <{from_email}>")
        
        # Send email if we have a sender address
        if from_email:
            print(f"[EMAIL] Preparing to send email to {ticket.guest_email}")
            
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from email.utils import make_msgid
            
            # Generate unique Message-ID for email threading
            message_id = make_msgid(domain=from_email.split('@')[1])
            
            msg = MIMEMultipart('alternative')
            msg['From'] = f"{from_name} <{from_email}>"
            msg['To'] = ticket.guest_email
            msg['Subject'] = f"Re: Ticket #{ticket.ticket_number} - {ticket.subject}"
            msg['Reply-To'] = from_email
            msg['Message-ID'] = message_id
            
            # Build email body
            commenter_name = user.full_name or user.username if user else "Support Team"
            
            email_body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #2563eb; border-bottom: 2px solid #2563eb; padding-bottom: 10px;">
            New Update on Your Ticket
        </h2>
        
        <div style="background-color: #f3f4f6; padding: 15px; border-radius: 5px; margin: 20px 0;">
            <p><strong>Ticket Number:</strong> #{ticket.ticket_number}</p>
            <p><strong>Subject:</strong> {ticket.subject}</p>
            <p><strong>Status:</strong> {ticket.status.title()}</p>
        </div>
        
        <div style="background-color: #fff; padding: 20px; border-left: 4px solid #2563eb; margin: 20px 0;">
            <p><strong>{commenter_name} commented:</strong></p>
            <div style="margin-top: 10px;">
                {content.replace(chr(10), '<br>')}
            </div>
        </div>
        
        <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb;">
            <p style="color: #6b7280; font-size: 14px;">
                You can reply directly to this email and your response will be added to the ticket.
            </p>
            <p style="color: #6b7280; font-size: 12px; margin-top: 20px;">
                This is an automated message from {from_name}. Please do not reply to this email if you have no further questions.
            </p>
        </div>
    </div>
</body>
</html>
"""
            
            msg.attach(MIMEText(email_body, 'html'))
            
            print(f"[EMAIL] Email message prepared, attempting to send via SMTP...")
            
            # Send email in thread pool to avoid blocking
            import concurrent.futures
            loop = asyncio.get_event_loop()
            
            def send_email():
                print(f"[EMAIL] Connecting to SMTP server {email_settings.smtp_host}:{email_settings.smtp_port}")
                if email_settings.smtp_use_tls:
                    server = smtplib.SMTP(email_settings.smtp_host, email_settings.smtp_port)
                    server.starttls()
                else:
                    server = smtplib.SMTP_SSL(email_settings.smtp_host, email_settings.smtp_port)
                
                print(f"[EMAIL] Logging in as {email_settings.smtp_username}")
                server.login(email_settings.smtp_username, email_settings.smtp_password)
                print(f"[EMAIL] Sending message...")
                server.send_message(msg)
                server.quit()
                print(f"[EMAIL] SMTP connection closed successfully")
            
            with concurrent.futures.ThreadPoolExecutor() as pool:
                await loop.run_in_executor(pool, send_email)
            
            # Store the Message-ID so replies can be threaded
            from app.models.processed_mail import ProcessedMail
            processed = ProcessedMail(
                workspace_id=ticket.workspace_id,
                message_id=message_id,
                email_from=from_email,
                subject=msg['Subject'],
                ticket_id=ticket.id,
                processed_at=get_local_time()
            )
            db.add(processed)
            await db.commit()
            
            print(f"✅ Sent email notification to {ticket.guest_email} from {from_email} with Message-ID: {message_id}")
    except Exception as e:
        print(f"❌ Error sending email notification: {e}")
        # Don't fail if email fails


@router.post('/tickets/{ticket_id}/update-status')
async def web_tickets_update_status(
    request: Request,
    ticket_id: int,
    status: str = Form(...),
    db: AsyncSession = Depends(get_session)
):
    """Update ticket status"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        return RedirectResponse('/web/login', status_code=303)
    
    from app.models.ticket import Ticket, TicketHistory
    from datetime import datetime
    
    ticket = (await db.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
    if not ticket:
        return RedirectResponse('/web/tickets', status_code=303)
    
    old_status = ticket.status
    ticket.status = status
    ticket.updated_at = datetime.utcnow()
    
    # Set resolved/closed timestamps and track who closed it
    if status == 'resolved' and not ticket.resolved_at:
        ticket.resolved_at = datetime.utcnow()
    elif status == 'closed':
        if not ticket.closed_at:
            ticket.closed_at = datetime.utcnow()
        ticket.closed_by_id = user_id  # Track who closed the ticket
        # Auto-archive when closed
        ticket.is_archived = True
        ticket.archived_at = datetime.utcnow()
    
    # Add history
    history = TicketHistory(
        ticket_id=ticket_id,
        user_id=user_id,
        action='status_changed',
        old_value=old_status,
        new_value=status
    )
    db.add(history)
    
    # Notify assigned user
    if ticket.assigned_to_id and ticket.assigned_to_id != user_id:
        notification = Notification(
            user_id=ticket.assigned_to_id,
            type='ticket',
            message=f'{user.full_name or user.username} changed ticket #{ticket.ticket_number} status to {status}',
            url=f'/web/tickets/{ticket_id}',
            related_id=ticket_id
        )
        db.add(notification)
    
    await db.commit()
    return RedirectResponse(f'/web/tickets/{ticket_id}', status_code=303)


@router.post('/tickets/{ticket_id}/archive')
async def web_tickets_archive(
    request: Request,
    ticket_id: int,
    db: AsyncSession = Depends(get_session)
):
    """Archive a ticket - Admin only"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    # Check if user is admin
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        request.session['error_message'] = 'Only administrators can archive tickets.'
        return RedirectResponse(f'/web/tickets/{ticket_id}', status_code=303)
    
    from app.models.ticket import Ticket, TicketHistory
    from datetime import datetime
    
    ticket = (await db.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
    if not ticket:
        return RedirectResponse('/web/tickets', status_code=303)
    
    # Archive the ticket
    ticket.is_archived = True
    ticket.archived_at = datetime.utcnow()
    ticket.updated_at = datetime.utcnow()
    
    # Add history entry
    history = TicketHistory(
        ticket_id=ticket_id,
        user_id=user_id,
        action='archived',
        new_value='Ticket archived by admin'
    )
    db.add(history)
    
    await db.commit()
    
    request.session['success_message'] = f'Ticket #{ticket.ticket_number} has been archived.'
    return RedirectResponse('/web/tickets/archived', status_code=303)


@router.post('/tickets/{ticket_id}/restore')
async def web_tickets_restore(
    request: Request,
    ticket_id: int,
    db: AsyncSession = Depends(get_session)
):
    """Restore archived ticket - Admin only"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    # Check if user is admin
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        request.session['error_message'] = 'Only administrators can restore archived tickets.'
        return RedirectResponse('/web/tickets/archived', status_code=303)
    
    from app.models.ticket import Ticket
    from datetime import datetime
    
    ticket = (await db.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
    if not ticket:
        return RedirectResponse('/web/tickets/archived', status_code=303)
    
    # Restore ticket
    ticket.is_archived = False
    ticket.archived_at = None
    ticket.updated_at = datetime.utcnow()
    
    await db.commit()
    request.session['success_message'] = f'Ticket #{ticket.ticket_number} has been restored.'
    return RedirectResponse('/web/tickets/archived', status_code=303)


@router.post('/tickets/{ticket_id}/assign')
async def web_tickets_assign(
    request: Request,
    ticket_id: int,
    assigned_to_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_session)
):
    """Assign ticket to user"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        return RedirectResponse('/web/login', status_code=303)
    
    from app.models.ticket import Ticket, TicketHistory
    from datetime import datetime
    
    ticket = (await db.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
    if not ticket:
        return RedirectResponse('/web/tickets', status_code=303)
    
    old_assigned = ticket.assigned_to_id
    ticket.assigned_to_id = assigned_to_id
    ticket.updated_at = datetime.utcnow()
    
    # Add history
    history = TicketHistory(
        ticket_id=ticket_id,
        user_id=user_id,
        action='assigned',
        old_value=str(old_assigned) if old_assigned else 'Unassigned',
        new_value=str(assigned_to_id) if assigned_to_id else 'Unassigned'
    )
    db.add(history)
    
    # Notify assigned user
    if assigned_to_id and assigned_to_id != user_id:
        notification = Notification(
            user_id=assigned_to_id,
            type='ticket',
            message=f'{user.full_name or user.username} assigned you ticket #{ticket.ticket_number}: {ticket.subject}',
            url=f'/web/tickets/{ticket_id}',
            related_id=ticket_id
        )
        db.add(notification)
    
    await db.commit()
    return RedirectResponse(f'/web/tickets/{ticket_id}', status_code=303)


@router.post('/tickets/process-emails')
async def web_tickets_process_emails(request: Request, db: AsyncSession = Depends(get_session)):
    """Manually trigger email-to-ticket processing (admin only) - V2"""
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_admin:
        return RedirectResponse('/web/tickets', status_code=303)
    
    try:
        from app.core.email_to_ticket_v2 import process_workspace_emails
        
        # Process emails using database settings (V2)
        tickets = await process_workspace_emails(db, user.workspace_id)
        
        # Show success message
        request.session['flash_message'] = f"✓ Processed {len(tickets)} emails and created {len(tickets)} tickets"
        request.session['flash_type'] = 'success'
        
    except Exception as e:
        request.session['flash_message'] = f"✗ Error processing emails: {str(e)}"
        request.session['flash_type'] = 'error'
    
    return RedirectResponse('/web/tickets', status_code=303)


# Chats
@router.get('/chats')
async def web_chats_list(request: Request, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Get all chats where user is a member
    stmt = (
        select(Chat)
        .join(ChatMember, Chat.id == ChatMember.chat_id)
        .where(ChatMember.user_id == user_id)
        .order_by(Chat.created_at.desc())
    )
    chats = (await db.execute(stmt)).scalars().all()
    
    # Get all active workspace users for creating new chats
    users = (await db.execute(
        select(User)
        .where(User.workspace_id == user.workspace_id, User.id != user_id, User.is_active == True)
        .order_by(User.full_name, User.email)
    )).scalars().all()
    
    return templates.TemplateResponse('chats/list.html', {
        'request': request,
        'chats': chats,
        'users': users,
        'user': user
    })


@router.post('/chats/create')
async def web_chat_create(
    request: Request,
    name: Optional[str] = Form(None),
    is_group: bool = Form(False),
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Create chat
    chat = Chat(
        name=name,
        is_group=is_group,
        workspace_id=user.workspace_id,
        created_by_id=user_id  # Set the creator
    )
    db.add(chat)
    await db.flush()
    
    # Add creator as member
    member = ChatMember(chat_id=chat.id, user_id=user_id)
    db.add(member)
    
    # Add selected members
    form_data = await request.form()
    member_ids = form_data.getlist('member_ids')
    for member_id in member_ids:
        if int(member_id) != user_id:
            member = ChatMember(chat_id=chat.id, user_id=int(member_id))
            db.add(member)
    
    await db.commit()
    return RedirectResponse(f'/web/chats/{chat.id}', status_code=303)


@router.get('/chats/{chat_id}')
async def web_chat_detail(request: Request, chat_id: int, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Verify user is member of this chat
    membership = (await db.execute(
        select(ChatMember)
        .where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id)
    )).scalar_one_or_none()
    
    if not membership:
        raise HTTPException(status_code=403, detail='Not a member of this chat')
    
    # Get chat details
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=404, detail='Chat not found')
    
    # Get all messages
    messages_stmt = (
        select(Message, User.full_name, User.email)
        .join(User, Message.author_id == User.id)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at.asc())
    )
    results = (await db.execute(messages_stmt)).all()
    
    # Get attachments for all messages
    from app.models.chat import MessageAttachment
    message_ids = [msg.id for msg, _, _ in results]
    attachments_stmt = (
        select(MessageAttachment)
        .where(MessageAttachment.message_id.in_(message_ids) if message_ids else False)
        .order_by(MessageAttachment.uploaded_at.asc())
    )
    all_attachments = (await db.execute(attachments_stmt)).scalars().all()
    
    # Group attachments by message_id
    attachments_by_message = {}
    for att in all_attachments:
        attachments_by_message.setdefault(att.message_id, []).append(att)
    
    # Combine messages with sender names and attachments
    messages_with_sender = [
        (msg, full_name or email, attachments_by_message.get(msg.id, []))
        for msg, full_name, email in results
    ]
    
    # Get chat members
    members_stmt = (
        select(User)
        .join(ChatMember, User.id == ChatMember.user_id)
        .where(ChatMember.chat_id == chat_id)
        .order_by(User.full_name, User.email)
    )
    members = (await db.execute(members_stmt)).scalars().all()
    
    return templates.TemplateResponse('chats/detail.html', {
        'request': request,
        'chat': chat,
        'messages': messages_with_sender,
        'members': members,
        'user': user
    })


@router.get('/chats/attachments/{attachment_id}/download')
async def download_chat_attachment(
    request: Request,
    attachment_id: int,
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    
    from app.models.chat import MessageAttachment
    from fastapi.responses import FileResponse
    import os
    
    # Get attachment
    attachment = (await db.execute(
        select(MessageAttachment).where(MessageAttachment.id == attachment_id)
    )).scalar_one_or_none()
    
    if not attachment:
        raise HTTPException(status_code=404, detail='Attachment not found')
    
    # Verify user has access to this chat
    message = (await db.execute(select(Message).where(Message.id == attachment.message_id))).scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail='Message not found')
    
    membership = (await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == message.chat_id,
            ChatMember.user_id == user_id
        )
    )).scalar_one_or_none()
    
    if not membership:
        raise HTTPException(status_code=403, detail='Access denied')
    
    if not os.path.exists(attachment.file_path):
        raise HTTPException(status_code=404, detail='File not found on disk')
    
    return FileResponse(
        path=attachment.file_path,
        filename=attachment.filename,
        media_type=attachment.mime_type or 'application/octet-stream'
    )


@router.post('/chats/{chat_id}/messages')
async def web_chat_send_message(
    request: Request,
    chat_id: int,
    content: Optional[str] = Form(None),
    attachments: Optional[list[UploadFile]] = File(None),
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Verify membership
    membership = (await db.execute(
        select(ChatMember)
        .where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id)
    )).scalar_one_or_none()
    
    if not membership:
        raise HTTPException(status_code=403, detail='Not a member of this chat')
    
    # Require either content or attachments
    if not content and not attachments:
        return RedirectResponse(f'/web/chats/{chat_id}', status_code=303)
    
    # Create message
    message = Message(
        chat_id=chat_id,
        author_id=user_id,
        content=content or ""
    )
    db.add(message)
    await db.flush()  # Get message ID
    
    # Handle file attachments
    if attachments:
        import uuid
        from pathlib import Path
        
        upload_dir = Path('app/uploads/chat_messages')
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        for file in attachments:
            if file.filename:
                # Generate unique filename
                file_ext = Path(file.filename).suffix
                unique_filename = f"{uuid.uuid4()}{file_ext}"
                file_path = upload_dir / unique_filename
                
                # Save file
                with open(file_path, 'wb') as f:
                    content_bytes = await file.read()
                    f.write(content_bytes)
                
                # Create attachment record
                from app.models.chat import MessageAttachment
                attachment = MessageAttachment(
                    message_id=message.id,
                    filename=file.filename,
                    file_path=str(file_path),
                    file_size=len(content_bytes),
                    mime_type=file.content_type
                )
                db.add(attachment)
    
    # Create notifications for other chat members
    chat_members = (await db.execute(
        select(ChatMember)
        .where(ChatMember.chat_id == chat_id, ChatMember.user_id != user_id)
    )).scalars().all()
    
    # Get chat info for notification message
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    
    # Build intelligent notification message
    chat_name = chat.name if chat and chat.name else 'chat'
    sender_name = user.full_name or user.username
    
    # Create message preview (keep it short)
    message_preview = ""
    if content:
        # Truncate long messages
        preview_text = content.strip()
        if len(preview_text) > 50:
            preview_text = preview_text[:47] + "..."
        message_preview = f": {preview_text}"
    
    # Check for attachments and add to summary
    attachment_summary = ""
    if attachments and len(attachments) > 0:
        attachment_count = len(attachments)
        
        # Analyze attachment types
        image_count = sum(1 for f in attachments if f.content_type and f.content_type.startswith('image/'))
        video_count = sum(1 for f in attachments if f.content_type and f.content_type.startswith('video/'))
        doc_count = sum(1 for f in attachments if f.content_type and (
            'pdf' in (f.content_type or '') or 
            'document' in (f.content_type or '') or
            'word' in (f.content_type or '') or
            'sheet' in (f.content_type or '') or
            'text' in (f.content_type or '')
        ))
        
        # Build attachment description
        attachment_parts = []
        if image_count > 0:
            attachment_parts.append(f"{image_count} image{'s' if image_count > 1 else ''}")
        if video_count > 0:
            attachment_parts.append(f"{video_count} video{'s' if video_count > 1 else ''}")
        if doc_count > 0:
            attachment_parts.append(f"{doc_count} document{'s' if doc_count > 1 else ''}")
        
        # If there are other files not categorized
        other_count = attachment_count - (image_count + video_count + doc_count)
        if other_count > 0:
            attachment_parts.append(f"{other_count} file{'s' if other_count > 1 else ''}")
        
        if attachment_parts:
            attachment_summary = f" [{', '.join(attachment_parts)}]"
        else:
            attachment_summary = f" [{attachment_count} attachment{'s' if attachment_count > 1 else ''}]"
    
    # Combine everything into a concise notification
    if message_preview and attachment_summary:
        notification_text = f"{sender_name}{message_preview} {attachment_summary}"
    elif attachment_summary:
        notification_text = f"{sender_name} sent {attachment_summary.strip('[]')} in {chat_name}"
    elif message_preview:
        notification_text = f"{sender_name} in {chat_name}{message_preview}"
    else:
        notification_text = f"{sender_name} sent a message in {chat_name}"
    
    for member in chat_members:
        # Create notification for each member
        notification = Notification(
            user_id=member.user_id,
            type='message',
            message=notification_text,
            url=f'/web/chats/{chat_id}',
            related_id=message.id
        )
        db.add(notification)
    
    await db.commit()
    
    return RedirectResponse(f'/web/chats/{chat_id}', status_code=303)


# Invite Users
@router.get('/users/new')
async def web_invite_user_form(request: Request, db: AsyncSession = Depends(get_session)):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Get current workspace users
    users = (await db.execute(
        select(User)
        .where(User.workspace_id == user.workspace_id)
        .order_by(User.full_name, User.email)
    )).scalars().all()
    
    return templates.TemplateResponse('users/invite.html', {
        'request': request,
        'users': users,
        'user': user,
        'error': None,
        'success': None
    })


@router.post('/users/invite')
async def web_invite_user(
    request: Request,
    email: str = Form(...),
    full_name: Optional[str] = Form(None),
    is_admin: bool = Form(False),
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    # Check if user with email already exists
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    
    users = (await db.execute(
        select(User)
        .where(User.workspace_id == user.workspace_id)
        .order_by(User.full_name, User.email)
    )).scalars().all()
    
    if existing:
        return templates.TemplateResponse('users/invite.html', {
            'request': request,
            'users': users,
            'user': user,
            'error': 'User with this email already exists',
            'success': None
        }, status_code=400)
    
    # Create new user in the same workspace with a temporary password
    import secrets
    temp_password = secrets.token_urlsafe(16)
    
    # Generate username from email (before @ symbol)
    username_base = email.split('@')[0].lower()
    # Check if username exists, add number if needed
    username = username_base
    counter = 1
    while True:
        existing_username = (await db.execute(
            select(User).where(User.username == username)
        )).scalar_one_or_none()
        if not existing_username:
            break
        username = f"{username_base}{counter}"
        counter += 1
    
    new_user = User(
        username=username,
        email=email,
        full_name=full_name or '',
        hashed_password=get_password_hash(temp_password),
        workspace_id=user.workspace_id,
        is_admin=is_admin,
        email_verified=True,  # OTP disabled
        profile_completed=True  # Skip profile completion for invited users
    )
    db.add(new_user)
    await db.commit()
    
    # In a real app, you'd send an email with the temp password or invitation link
    # For now, we'll just show a success message
    users = (await db.execute(
        select(User)
        .where(User.workspace_id == user.workspace_id)
        .order_by(User.full_name, User.email)
    )).scalars().all()
    
    return templates.TemplateResponse('users/invite.html', {
        'request': request,
        'users': users,
        'user': user,
        'error': None,
        'success': f'User invited successfully! Username: {username}, Temporary password: {temp_password} (share these securely)'
    })


@router.post('/users/{user_id}/delete')
async def web_delete_user(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_session)
):
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return RedirectResponse('/web/login', status_code=303)
    current_user = (await db.execute(select(User).where(User.id == current_user_id))).scalar_one_or_none()
    if not current_user or not current_user.is_admin:
        return RedirectResponse('/web/projects', status_code=303)
    
    # Get user to delete
    user_to_delete = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user_to_delete or user_to_delete.workspace_id != current_user.workspace_id:
        return RedirectResponse('/web/users/new', status_code=303)
    
    # Prevent deleting yourself
    if user_to_delete.id == current_user.id:
        return RedirectResponse('/web/users/new', status_code=303)
    
    # Deactivate user instead of deleting (preserves audit trail)
    user_to_delete.is_active = False
    await db.commit()
    
    return RedirectResponse('/web/users/new', status_code=303)


@router.get('/set-password')
async def web_set_password_form(request: Request, token: Optional[str] = None):
    return templates.TemplateResponse('auth/set_password.html', {
        'request': request,
        'token': token,
        'error': None
    })


@router.post('/set-password')
async def web_set_password(
    request: Request,
    username: str = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_session)
):
    # Validate passwords match
    if new_password != confirm_password:
        return templates.TemplateResponse('auth/set_password.html', {
            'request': request,
            'token': None,
            'error': 'New passwords do not match'
        }, status_code=400)
    
    # Find user by username
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if not user:
        return templates.TemplateResponse('auth/set_password.html', {
            'request': request,
            'token': None,
            'error': 'Invalid username or password'
        }, status_code=400)
    
    # Verify current password
    if not verify_password(current_password, user.hashed_password):
        return templates.TemplateResponse('auth/set_password.html', {
            'request': request,
            'token': None,
            'error': 'Invalid username or password'
        }, status_code=400)
    
    # Update password
    user.hashed_password = get_password_hash(new_password)
    await db.commit()
    
    # Auto-login after password change
    request.session['user_id'] = user.id
    
    # Redirect to profile completion if needed
    if not user.profile_completed:
        return RedirectResponse('/web/profile/complete', status_code=303)
    
    return RedirectResponse('/web/projects', status_code=303)


@router.get('/activity')
async def web_activity_feed(
    request: Request,
    activity_type: Optional[str] = None,
    db: AsyncSession = Depends(get_session)
):
    user_id = request.session.get('user_id')
    if not user_id:
        return RedirectResponse('/web/login', status_code=303)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        request.session.clear()
        return RedirectResponse('/web/login', status_code=303)
    
    from datetime import datetime, timedelta
    from app.models.task_extensions import ActivityLog
    
    # Get recent activity logs
    query = select(ActivityLog).where(ActivityLog.workspace_id == user.workspace_id)
    if activity_type:
        query = query.where(ActivityLog.action_type == activity_type)
    query = query.order_by(ActivityLog.created_at.desc()).limit(100)
    
    logs = (await db.execute(query)).scalars().all()
    
    # Enhance activity logs with user names and entity titles
    activities = []
    for log in logs:
        # Get user who performed action
        actor = (await db.execute(select(User).where(User.id == log.user_id))).scalar_one_or_none()
        
        # Get entity title based on type
        entity_title = None
        if log.entity_type == 'task':
            task = (await db.execute(select(Task).where(Task.id == log.entity_id))).scalar_one_or_none()
            entity_title = task.title if task else None
        elif log.entity_type == 'project':
            project = (await db.execute(select(Project).where(Project.id == log.entity_id))).scalar_one_or_none()
            entity_title = project.name if project else None
        
        # Calculate time ago
        time_diff = datetime.utcnow() - log.created_at
        if time_diff.total_seconds() < 60:
            time_ago = "just now"
        elif time_diff.total_seconds() < 3600:
            time_ago = f"{int(time_diff.total_seconds() / 60)}m ago"
        elif time_diff.total_seconds() < 86400:
            time_ago = f"{int(time_diff.total_seconds() / 3600)}h ago"
        else:
            time_ago = f"{int(time_diff.total_seconds() / 86400)}d ago"
        
        activities.append({
            'user_name': actor.full_name or actor.email if actor else 'Unknown',
            'action_type': log.action_type,
            'action_text': log.action_type.replace('_', ' '),
            'entity_type': log.entity_type,
            'entity_id': log.entity_id,
            'entity_title': entity_title,
            'details': log.details,
            'time_ago': time_ago,
            'created_at': log.created_at
        })
    
    # Get workspace stats
    today = datetime.utcnow().date()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    
    active_tasks = (await db.execute(
        select(Task)
        .join(Project, Task.project_id == Project.id)
        .where(Project.workspace_id == user.workspace_id)
        .where(Task.status != TaskStatus.done)
    )).scalars().all()
    
    completed_week = (await db.execute(
        select(Task)
        .join(Project, Task.project_id == Project.id)
        .where(Project.workspace_id == user.workspace_id)
        .where(Task.status == TaskStatus.done)
        .where(Task.updated_at >= datetime.combine(week_ago, datetime.min.time()))
    )).scalars().all()
    
    overdue = (await db.execute(
        select(Task)
        .join(Project, Task.project_id == Project.id)
        .where(Project.workspace_id == user.workspace_id)
        .where(Task.status != TaskStatus.done)
        .where(Task.due_date < today)
    )).scalars().all()
    
    team_members = (await db.execute(
        select(User)
        .where(User.workspace_id == user.workspace_id)
        .where(User.is_active == True)
    )).scalars().all()
    
    # Calculate per-user statistics
    user_stats = []
    
    if user.is_admin:
        # Admin sees all users' statistics
        for member in team_members:
            # Get tasks assigned to this user
            member_assignments = (await db.execute(
                select(Assignment)
                .where(Assignment.assignee_id == member.id)
            )).scalars().all()
            
            task_ids = [a.task_id for a in member_assignments]
            
            if task_ids:
                # Get all tasks for this user
                member_tasks = (await db.execute(
                    select(Task)
                    .where(Task.id.in_(task_ids))
                )).scalars().all()
                
                # Tasks completed in the last month
                completed_month = [t for t in member_tasks 
                                  if t.status == TaskStatus.done 
                                  and t.updated_at 
                                  and t.updated_at.date() >= month_ago]
                
                # Tasks completed late (had due date, completed after due date)
                completed_late = [t for t in completed_month
                                 if t.due_date and t.updated_at 
                                 and t.updated_at.date() > t.due_date]
                
                # Currently overdue tasks
                overdue_tasks = [t for t in member_tasks
                                if t.status != TaskStatus.done
                                and t.due_date
                                and t.due_date < today]
                
                # Currently active (in progress) tasks
                active_member_tasks = [t for t in member_tasks
                                      if t.status == TaskStatus.in_progress]
                
                # Get current task (most recently updated in-progress task)
                current_task = None
                if active_member_tasks:
                    current_task = sorted(active_member_tasks, 
                                        key=lambda x: x.updated_at if x.updated_at else x.created_at,
                                        reverse=True)[0]
                
                user_stats.append({
                    'user_id': member.id,
                    'user_name': member.full_name or member.email,
                    'user_email': member.email,
                    'completed_month': len(completed_month),
                    'completed_late': len(completed_late),
                    'overdue_count': len(overdue_tasks),
                    'active_count': len(active_member_tasks),
                    'current_task': current_task.title if current_task else None,
                    'current_task_id': current_task.id if current_task else None
                })
    else:
        # Regular user sees only their own statistics
        member_assignments = (await db.execute(
            select(Assignment)
            .where(Assignment.assignee_id == user.id)
        )).scalars().all()
        
        task_ids = [a.task_id for a in member_assignments]
        
        if task_ids:
            member_tasks = (await db.execute(
                select(Task)
                .where(Task.id.in_(task_ids))
            )).scalars().all()
            
            # Tasks completed in the last month
            completed_month = [t for t in member_tasks 
                              if t.status == TaskStatus.done 
                              and t.updated_at 
                              and t.updated_at.date() >= month_ago]
            
            # Separate completed on time vs late
            completed_on_time = []
            completed_late = []
            
            for t in completed_month:
                if t.due_date and t.updated_at:
                    if t.updated_at.date() > t.due_date:
                        completed_late.append(t)
                    else:
                        completed_on_time.append(t)
                else:
                    # No due date set, just mark as completed
                    completed_on_time.append(t)
            
            # Currently overdue tasks
            overdue_tasks = [t for t in member_tasks
                            if t.status != TaskStatus.done
                            and t.due_date
                            and t.due_date < today]
            
            # Currently active tasks
            active_member_tasks = [t for t in member_tasks
                                  if t.status == TaskStatus.in_progress]
            
            user_stats.append({
                'user_id': user.id,
                'user_name': user.full_name or user.email,
                'user_email': user.email,
                'completed_month': len(completed_month),
                'completed_on_time': len(completed_on_time),
                'completed_late': len(completed_late),
                'completed_late_tasks': completed_late,
                'overdue_count': len(overdue_tasks),
                'overdue_tasks': overdue_tasks,
                'active_count': len(active_member_tasks)
            })
    
    stats = {
        'active_tasks': len(active_tasks),
        'completed_week': len(completed_week),
        'overdue': len(overdue),
        'team_members': len(team_members)
    }
    
    return templates.TemplateResponse('activity/feed.html', {
        'request': request,
        'user': user,
        'activities': activities,
        'stats': stats,
        'user_stats': user_stats
    })
