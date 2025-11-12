from fastapi import FastAPI, Request, Depends, HTTPException
# restart trigger: updated timestamp
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

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

# Session middleware for server-rendered web UI
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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
    # Render the landing page template
    return templates.TemplateResponse("index.html", {"request": request})
