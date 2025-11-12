from typing import Optional

from fastapi import Request, HTTPException, status


def get_session_user_id(request: Request) -> Optional[int]:
    user_id = request.session.get("user_id")
    return user_id


def require_session_user_id(request: Request) -> int:
    user_id = get_session_user_id(request)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_302_FOUND, detail="Redirect to login")
    return user_id
