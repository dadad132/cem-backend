from fastapi import Depends, FastAPI, Request
# restart trigger: updated timestamp
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from urllib.parse import unquote
import posixpath
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
try:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
except Exception:  # pragma: no cover - older uvicorn fallback
    ProxyHeadersMiddleware = None  # type: ignore[assignment]
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
import os

from app.core.config import get_settings
from app.core.database import get_session, lifespan
from app.api.routes import auth as auth_routes
from app.api.routes import users as users_routes
from app.api.routes import projects as projects_routes
from app.api.routes import tasks as tasks_routes
from app.models.user import User

settings = get_settings()

# Disable default API docs - we'll add custom protected ones
app = FastAPI(
    title=settings.app_name, 
    debug=settings.debug, 
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)

# GZip compression for faster page loads (compresses responses > 500 bytes).
# Level 6 instead of the default 9: about 3x less CPU on big pages for a
# few percent more bytes (a long ticket: 13 ms instead of 44 ms, 93 vs 90 KB).
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)

_cors_origins = settings.cors_origins
_allow_credentials = "*" not in _cors_origins  # credentials=True requires explicit origins, not "*"
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pages an account without a company may still open. The super admin is a
# server operator and belongs to no company; every other page is company data.
_NO_COMPANY_PATHS = (
    '/web/superadmin', '/web/login', '/web/logout', '/web/signup', '/web/set-password',
    '/web/download', '/web/notifications', '/web/uploads/',
    '/web/client/remote/get/', '/web/remote/get/',
)

# With no company the super admin still has its own projects and tasks: what it
# makes there has no company either. The rest is a company's (tickets, chats,
# users, meetings...) and would crash or come out broken without one.
_SUPERADMIN_OWN_WORK_PATHS = (
    '/web/dashboard', '/web/activity', '/web/tasks', '/web/my-tasks', '/web/projects', '/web/calendar',
    '/web/workload', '/web/profile', '/web/search', '/web/attachments', '/web/task-attachments', '/web/api/',
)

_NO_COMPANY_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>No company</title>
<style>
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:1rem;
         font-family:system-ui, sans-serif; background:#f1f5f9; color:#0f172a; }
  .box { max-width:28rem; background:#fff; border-radius:12px; padding:2rem; box-shadow:0 10px 30px rgba(0,0,0,.08); }
  h1 { font-size:1.25rem; margin:0 0 .5rem; }
  button { margin-top:1rem; padding:.6rem 1.2rem; border:0; border-radius:8px; background:#0f172a; color:#fff;
           font-weight:600; cursor:pointer; }
</style></head>
<body><div class="box">
  <h1>Your account is not part of a company yet</h1>
  <p>Ask your administrator to add it to your company. Until then you cannot use tickets, tasks or chats.</p>
  <form method="post" action="/web/logout"><button type="submit">Sign out</button></form>
</div></body></html>"""


def page_for_account_without_company(path: str, user):
    """Where an account with no company goes instead of a company page; None to carry on.

    Company pages assume a company. Without one they created users with no
    company (the super admin's Create User) and crashed on save, e.g. a chat
    with no workspace_id. The super admin keeps its own projects and tasks, and
    goes back to its console for the rest, where it can choose a company to work in.
    """
    if not path.startswith('/web/') or path.startswith(_NO_COMPANY_PATHS):
        return None
    if user.is_superadmin:
        if path.startswith(_SUPERADMIN_OWN_WORK_PATHS):
            return None
        # The console is where it picks the company to work in
        return RedirectResponse('/web/superadmin?choose_company=1', status_code=303)
    return HTMLResponse(_NO_COMPANY_PAGE, status_code=403)


# Workspace injection middleware - adds workspace to all requests
# MUST be added BEFORE SessionMiddleware so it runs AFTER (middleware order is reversed)
from starlette.middleware.base import BaseHTTPMiddleware

class WorkspaceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip workspace lookup for static files, health checks, and API calls
        path = request.url.path
        if path.startswith('/static') or path.startswith('/uploads') or path == '/health' or path.startswith('/api/'):
            return await call_next(request)
        
        user_id = None
        
        # Safely check for session
        try:
            if "session" in request.scope:
                user_id = request.scope["session"].get('user_id')
        except Exception:
            pass
        
        blocked = None
        if user_id:
            try:
                from app.core.database import get_session
                from app.models.workspace import Workspace
                async for db in get_session():
                    user = (await db.execute(
                        select(User).where(User.id == user_id)
                    )).scalar_one_or_none()

                    if user and user.workspace_id:
                        workspace = (await db.execute(
                            select(Workspace).where(Workspace.id == user.workspace_id)
                        )).scalar_one_or_none()

                        if workspace:
                            request.state.workspace = workspace
                    elif user:
                        blocked = page_for_account_without_company(path, user)
                    break
            except Exception:
                pass
        if blocked is not None:
            return blocked

        response = await call_next(request)
        return response

app.add_middleware(WorkspaceMiddleware)

# Cache control middleware for static assets
class CacheControlMiddleware(BaseHTTPMiddleware):
    """Add cache headers to static files for better performance"""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        # Cache uploaded files (images, attachments) for 1 day
        if path.startswith('/uploads'):
            response.headers['Cache-Control'] = 'public, max-age=86400'
        # Cache static assets (JS, CSS, icons, manifest) for 7 days
        elif path.startswith('/static'):
            response.headers['Cache-Control'] = 'public, max-age=604800'
        return response

app.add_middleware(CacheControlMiddleware)

# Security headers middleware for HTTPS optimization
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Prevent clickjacking
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        # Prevent MIME type sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # The old browser XSS filter is gone from current browsers and could be
        # abused where it remains; current advice is to switch it off
        response.headers['X-XSS-Protection'] = '0'
        # Referrer policy
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Permissions policy
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        # HSTS ("only ever use HTTPS for this site") only on HTTPS responses:
        # browsers ignore it over plain HTTP. No includeSubDomains, which would
        # also force other names under the domain (e.g. a mail server) onto
        # HTTPS for a year. Behind nginx this needs uvicorn --proxy-headers,
        # which setup_domain_ssl.sh turns on.
        if not settings.debug and request.url.scheme == 'https':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Session middleware for server-rendered web UI
# In production: secure=True ensures cookies only sent over HTTPS
# httponly=True prevents JavaScript access to session cookie
# same_site='lax' provides CSRF protection while allowing normal navigation
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=settings.https_only,  # Only True when HTTPS is configured; False for plain HTTP cloud
    same_site='lax'
)

# Restrict Host headers only when ALLOWED_HOSTS is explicitly locked down (no "*").
# Default includes tickets.hubconnect.co.za alongside "*" so bare-IP fallback keeps working.
if settings.allowed_hosts and "*" not in settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

# Trust X-Forwarded-Proto / X-Forwarded-For from local reverse proxy (Nginx) so
# HTTPS scheme, HSTS, guest portal URLs, and real client IPs work cleanly on
# tickets.hubconnect.co.za even if uvicorn was launched without --proxy-headers.
if ProxyHeadersMiddleware is not None:
    _trusted_proxies = [
        h.strip() for h in (settings.forwarded_allow_ips or "").split(",") if h.strip()
    ] or ["127.0.0.1", "::1"]
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_trusted_proxies)


class PrivateUploadCacheMiddleware(BaseHTTPMiddleware):
    """The thumbnail cache lives under /uploads but is never publicly served.

    /uploads is a static mount with no session or workspace checks, so the
    private .thumbs directory must be denied before StaticFiles sees it.
    """

    @staticmethod
    def _is_private_thumbnail(request: Request) -> bool:
        # Check a normalized form of both the decoded and raw ASGI paths:
        # StaticFiles resolves dot segments, so a literal string prefix check
        # can be bypassed with %2e, // or ../ variants.
        candidates = []
        for raw in (
            request.scope.get('path', ''),
            request.scope.get('raw_path', b'').decode('latin-1'),
        ):
            decoded = unquote(unquote(raw)).replace('\\', '/')
            candidates.append(posixpath.normpath(decoded))
        return any(
            path == '/uploads/.thumbs' or path.startswith('/uploads/.thumbs/')
            for path in candidates
        )

    async def dispatch(self, request: Request, call_next):
        if self._is_private_thumbnail(request):
            return Response(status_code=404)
        return await call_next(request)


app.add_middleware(PrivateUploadCacheMiddleware)

# Import templates from web routes to get the enhanced version with workspace injection
from app.web.routes import templates

# Mount uploads directory for serving uploaded files (logos, attachments, etc.)
BASE_DIR = Path(__file__).resolve().parent
uploads_path = os.path.join(BASE_DIR, "uploads")
os.makedirs(uploads_path, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_path), name="uploads")

# Mount static directory for PWA assets (icons, manifest, service worker)
static_path = os.path.join(BASE_DIR, "static")
os.makedirs(static_path, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/health")
async def health():
    return {"status": "ok"}


# API routers
app.include_router(auth_routes.router, prefix="/api")
app.include_router(users_routes.router, prefix="/api")
app.include_router(projects_routes.router, prefix="/api")
app.include_router(tasks_routes.router, prefix="/api")
from app.api.routes import system as system_routes
app.include_router(system_routes.router, prefix="/api")
from app.api.routes import external as external_routes
app.include_router(external_routes.router, prefix="/api")
from app.web import routes as web_routes  # noqa: E402
app.include_router(web_routes.router, prefix="/web")
from app.web import meshcentral_routes as meshcentral_web_routes  # noqa: E402
app.include_router(meshcentral_web_routes.router, prefix="/web")


# Minimal server-rendered pages
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_session)):
    # A signed-in user skips the public landing page and goes straight to
    # their saved tab. Guards match the login route so nobody lands somewhere
    # they are not allowed to be.
    user_id = request.session.get('user_id')
    if user_id:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user or not user.is_active:
            request.session.clear()
        else:
            from app.web.routes import safe_landing_page

            if user.is_superadmin:
                return RedirectResponse('/web/superadmin', status_code=303)
            if not user.profile_completed:
                return RedirectResponse('/web/profile/complete', status_code=303)
            return RedirectResponse(safe_landing_page(user.default_landing_page), status_code=303)
    # Get workspace from request state (added by middleware)
    workspace = getattr(request.state, 'workspace', None)
    # Render the landing page template
    return templates.TemplateResponse("index.html", {
        "request": request,
        "workspace": workspace
    })
