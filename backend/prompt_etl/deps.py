"""Shared FastAPI dependencies.

Callers authenticate with ``Authorization: Bearer <access token>``. The token
is a server secret (``ACCESS_TOKEN``), not an identity the client invents.
``ALLOW_DEV_AUTH`` is a local-only switch that still accepts ``dev:<email>``.
"""

from __future__ import annotations

import hmac
import logging
import uuid
from typing import Annotated, AsyncIterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .arq_pool import get_arq_pool
from .config import Settings, get_settings
from .db.models import User, Workspace
from .db.session import get_session

log = logging.getLogger(__name__)


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def authorized_email(token: str, settings: Settings) -> str | None:
    """Email the token is allowed to act as, or None when it is not accepted."""
    if settings.allow_dev_auth and token.startswith("dev:"):
        email = token[len("dev:"):].strip().lower()
        return email if "@" in email else None
    expected = settings.access_token
    if expected and hmac.compare_digest(token, expected):
        email = settings.operator_email.strip().lower()
        return email if "@" in email else None
    return None


async def get_current_user(
    session: SessionDep,
    authorization: str | None = Header(default=None),
) -> User:
    """Resolve the caller from the access token and find-or-create their user."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid access token",
        )
    token = authorization.split(" ", 1)[1].strip()
    email = authorized_email(token, get_settings())
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid access token",
        )

    row = await session.execute(select(User).where(User.email == email))
    user = row.scalar_one_or_none()
    if user is None:
        user = User(email=email, display_name=email.split("@", 1)[0])
        session.add(user)
        await session.flush()
        ws = Workspace(name="default", owner_id=user.id)
        session.add(ws)
        await session.flush()
        log.info("auth: created user %s and default workspace", email)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_default_workspace(
    session: SessionDep, user: CurrentUser
) -> Workspace:
    row = await session.execute(
        select(Workspace).where(Workspace.owner_id == user.id).order_by(Workspace.created_at.asc())
    )
    ws = row.scalars().first()
    if ws is None:
        ws = Workspace(name="default", owner_id=user.id)
        session.add(ws)
        await session.flush()
    return ws


CurrentWorkspace = Annotated[Workspace, Depends(get_default_workspace)]


async def arq_pool():
    pool = await get_arq_pool()
    return pool
