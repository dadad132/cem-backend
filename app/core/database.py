from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from .config import get_settings

_settings = get_settings()

_initialized: bool = False

engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    echo=_settings.debug,
    future=True,
)

async_session_factory = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def init_models() -> None:
    # Import models to register tables
    from app import models  # noqa: F401
    async with engine.begin() as conn:
        # For SQLite dev usage: if schema drift is detected (e.g., missing columns
        # after model changes), drop and recreate all tables to avoid runtime errors.
        try:
            if engine.url.get_backend_name().startswith("sqlite"):
                # Drift check for user table (workspace_id) and task table (new scheduling columns)
                res_user = await conn.exec_driver_sql('PRAGMA table_info("user")')
                user_cols = [row[1] for row in res_user.fetchall()]
                need_rebuild = False
                # Rebuild if critical columns are missing
                for col in ("workspace_id", "preferred_meeting_platform", "email_verified", "verification_code", "verification_expires_at"):
                    if user_cols and col not in user_cols:
                        need_rebuild = True

                res_task = await conn.exec_driver_sql('PRAGMA table_info("task")')
                task_cols = [row[1] for row in res_task.fetchall()]
                expected_task_cols = {"start_date", "start_time", "due_date", "due_time"}
                if task_cols and not expected_task_cols.issubset(set(task_cols)):
                    need_rebuild = True

                # Note: project_member table check removed - table should be added via manual migration
                # to avoid losing data. The table structure is created by SQLModel.metadata.create_all
                # if it doesn't exist, without dropping existing tables.

                if need_rebuild:
                    await conn.run_sync(SQLModel.metadata.drop_all)
        except Exception:
            # Best-effort; continue to create_all
            pass
        await conn.run_sync(SQLModel.metadata.create_all)


async def ensure_initialized() -> None:
    global _initialized
    if not _initialized:
        await init_models()
        _initialized = True


@asynccontextmanager
async def lifespan(app):  # FastAPI lifespan
    # Initialize database
    await init_models()
    
    # Setup graceful shutdown handlers
    from app.core.shutdown import shutdown_handler
    shutdown_handler.setup_handlers()
    
    # Start automatic backup system
    from app.core.backup import backup_manager
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        await backup_manager.start_auto_backup()
        logger.info("✅ Automatic backup system started")
    except Exception as e:
        logger.error(f"⚠️  Failed to start backup system: {e}")
    
    # Start email-to-ticket scheduler (V2 - uses database settings)
    try:
        from app.core.email_scheduler_v2 import start_email_scheduler
        await start_email_scheduler()
        logger.info("✅ Email-to-Ticket scheduler started (V2 - database config)")
    except Exception as e:
        logger.warning(f"⚠️  Email-to-Ticket scheduler not started: {e}")
    
    yield
    
    # Cleanup on shutdown - execute graceful shutdown sequence
    try:
        logger.info("🛑 Application shutdown requested...")
        await shutdown_handler.shutdown_sequence()
        
        # Stop email scheduler
        from app.core.email_scheduler_v2 import stop_email_scheduler
        await stop_email_scheduler()
        logger.info("✅ Email-to-Ticket scheduler stopped")
    except Exception as e:
        logger.error(f"⚠️  Error during graceful shutdown: {e}")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an AsyncSession.

    Note: do NOT wrap this with @asynccontextmanager — FastAPI expects an async
    generator (uses `yield`) and will manage the context for the dependency.
    Returning the context manager object causes the dependency to be the
    context manager itself, which doesn't have DB methods like `execute`.
    """
    await ensure_initialized()
    async with async_session_factory() as session:
        yield session
