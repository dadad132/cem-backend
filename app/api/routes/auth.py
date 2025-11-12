from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.security import create_access_token, create_refresh_token, verify_password, get_password_hash
from app.models.user import User, UserCreate, UserRead
from app.models.workspace import Workspace

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect email or password")

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/signup", response_model=UserRead, status_code=201)
async def signup(data: UserCreate, db: AsyncSession = Depends(get_db)):
    exists = await db.execute(select(User).where(User.email == data.email))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    ws = Workspace(name=f"{data.full_name or data.email}'s Workspace")
    db.add(ws)
    await db.flush()

    user = User(email=data.email, full_name=data.full_name or "", hashed_password=get_password_hash(data.password), workspace_id=ws.id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserRead(id=user.id, email=user.email, full_name=user.full_name, is_active=user.is_active, is_admin=user.is_admin)
