from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.auth_service import auth_service
from app.services.session_scope_service import (
    AuthenticatedPrincipal,
    require_authenticated_principal,
    require_session_owner,
)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
async def login(request: LoginRequest):
    username = request.username.strip()
    if not username or not request.password:
        raise HTTPException(
            status_code=401,
            detail={
                "code": 401,
                "message": "username and password are required",
                "detail": "credentials_required",
            },
        )

    if not auth_service.authenticate(username, request.password):
        raise HTTPException(
            status_code=401,
            detail={
                "code": 401,
                "message": "invalid username or password",
                "detail": "bad_credentials",
            },
        )

    return {
        "code": 200,
        "message": "success",
        "data": {
            "token": auth_service.create_access_token(username),
            "username": username,
        },
    }


@router.get("/auth/me")
async def me(principal: AuthenticatedPrincipal = Depends(require_authenticated_principal)):
    """Probe whether the caller's access token is still valid."""

    return {
        "code": 200,
        "message": "success",
        "data": {
            "username": principal.username,
            "owner_key": principal.owner_key,
        },
    }


@router.post("/auth/logout")
async def logout(_owner_key: str = Depends(require_session_owner)):
    return {"code": 200, "message": "success", "data": {}}
