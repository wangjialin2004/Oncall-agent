from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.auth_service import auth_service
from app.services.session_scope_service import require_session_owner

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
async def login(request: LoginRequest):
    username = request.username.strip()
    if not username or not request.password:
        raise HTTPException(status_code=401, detail="username and password are required")

    return {
        "code": 200,
        "message": "success",
        "data": {
            "token": auth_service.create_access_token(username),
            "username": username,
        },
    }


@router.post("/auth/logout")
async def logout(_owner_key: str = Depends(require_session_owner)):
    return {"code": 200, "message": "success", "data": {}}
