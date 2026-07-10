"""Auth (dev token stub for Phase 1)."""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import CurrentUser, CurrentWorkspace

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def me(user: CurrentUser, workspace: CurrentWorkspace) -> dict:
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
        },
        "workspace": {
            "id": str(workspace.id),
            "name": workspace.name,
        },
    }
