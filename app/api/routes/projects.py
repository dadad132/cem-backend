from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.api.deps import get_current_user_id, get_db
from app.models.project import Project, ProjectCreate, ProjectRead, ProjectUpdate
from app.models.user import User

router = APIRouter(prefix="/projects", tags=["projects"]) 


@router.post("/", response_model=ProjectRead, status_code=201)
async def create_project(
    data: ProjectCreate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    # Assign project to the user's workspace
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    project = Project(name=data.name, description=data.description, owner_id=user_id, workspace_id=user.workspace_id)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.get("/", response_model=list[ProjectRead])
async def list_projects(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    q: Optional[str] = Query(None),
    sort: Optional[str] = Query("-created_at"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    # Filter by user's workspace
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    stmt = select(Project).where(Project.workspace_id == user.workspace_id)
    if q:
        stmt = stmt.where(col(Project.name).ilike(f"%{q}%"))
    if sort:
        order = desc if sort.startswith("-") else asc
        field = sort.lstrip("-")
        if field == "created_at":
            stmt = stmt.order_by(order(Project.created_at))
        elif field == "updated_at":
            stmt = stmt.order_by(order(Project.updated_at))
        else:
            stmt = stmt.order_by(order(Project.id))
    stmt = stmt.limit(limit).offset(offset)

    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(project_id: int, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    result = await db.execute(select(Project).where(Project.id == project_id, Project.workspace_id == user.workspace_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: int,
    data: ProjectUpdate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    result = await db.execute(select(Project).where(Project.id == project_id, Project.workspace_id == user.workspace_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: int, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    result = await db.execute(select(Project).where(Project.id == project_id, Project.workspace_id == user.workspace_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await db.delete(project)
    await db.commit()
    return None
