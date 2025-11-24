from fastapi import FastAPI, Request, Depends, HTTPException
# restart trigger: updated timestamp
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
import os

from app.core.config import get_settings
from app.core.database import lifespan, get_session
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Workspace injection middleware - adds workspace to all requests
# MUST be added BEFORE SessionMiddleware so it runs AFTER (middleware order is reversed)
from starlette.middleware.base import BaseHTTPMiddleware

class WorkspaceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Add workspace to request state for templates
        print(f"[DEBUG] WorkspaceMiddleware called for: {request.url.path}")
        user_id = None
        
        # Safely check for session - MUST check in request.scope, not hasattr
        try:
            # SessionMiddleware stores session in request.scope, not as attribute
            if "session" in request.scope:
                user_id = request.scope["session"].get('user_id')
                print(f"[DEBUG] Found user_id in session: {user_id}")
            else:
                print(f"[DEBUG] No session in request.scope")
        except Exception as e:
            print(f"[DEBUG] Session access error: {e}")
            pass
        
        if user_id:
            try:
                from app.core.database import get_session
                from app.models.workspace import Workspace
                async for db in get_session():
                    # Get user first to find workspace_id
                    user = (await db.execute(
                        select(User).where(User.id == user_id)
                    )).scalar_one_or_none()
                    
                    if user and user.workspace_id:
                        # Fetch workspace
                        workspace = (await db.execute(
                            select(Workspace).where(Workspace.id == user.workspace_id)
                        )).scalar_one_or_none()
                        
                        if workspace:
                            request.state.workspace = workspace
                            print(f"[DEBUG] Workspace SET: name={workspace.name}, logo={workspace.logo_url}, title={workspace.site_title}")
                        else:
                            print(f"[DEBUG] No workspace found for workspace_id={user.workspace_id}")
                        break
                    else:
                        print(f"[DEBUG] User found but no workspace_id: user={user}")
            except Exception as e:
                # Log error but continue
                print(f"[DEBUG] WorkspaceMiddleware exception: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"[DEBUG] No user_id in session")
        
        response = await call_next(request)
        return response

app.add_middleware(WorkspaceMiddleware)

# Session middleware for server-rendered web UI
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

# Import templates from web routes to get the enhanced version with workspace injection
from app.web.routes import templates

# Mount uploads directory for serving uploaded files (logos, attachments, etc.)
BASE_DIR = Path(__file__).resolve().parent
uploads_path = os.path.join(BASE_DIR, "uploads")
os.makedirs(uploads_path, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_path), name="uploads")


@app.get("/health")
async def health():
    return {"status": "ok"}


# API documentation routes - DISABLED for all users
# Documentation is completely hidden from all users including admins
# Uncomment routes below if you need to enable API docs temporarily

# @app.get("/docs", include_in_schema=False)
# async def custom_swagger_ui_html(request: Request):
#     """Swagger UI - Disabled"""
#     raise HTTPException(status_code=404, detail="Not found")


# @app.get("/redoc", include_in_schema=False)
# async def redoc_html(request: Request):
#     """ReDoc - Disabled"""
#     raise HTTPException(status_code=404, detail="Not found")


# @app.get("/openapi.json", include_in_schema=False)
# async def get_open_api_endpoint(request: Request):
#     """OpenAPI JSON - Disabled"""
#     raise HTTPException(status_code=404, detail="Not found")


# API routers
app.include_router(auth_routes.router, prefix="/api")
app.include_router(users_routes.router, prefix="/api")
app.include_router(projects_routes.router, prefix="/api")
app.include_router(tasks_routes.router, prefix="/api")
from app.api.routes import system as system_routes
app.include_router(system_routes.router, prefix="/api")
from app.web import routes as web_routes  # noqa: E402
app.include_router(web_routes.router, prefix="/web")


# Minimal server-rendered pages
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # Get workspace from request state (added by middleware)
    workspace = getattr(request.state, 'workspace', None)
    # Render the landing page template
    return templates.TemplateResponse("index.html", {
        "request": request,
        "workspace": workspace
    })
