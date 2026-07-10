"""Shared FastAPI dependencies.

In Phase 1 we use a 'dev token' style auth: the user passes
    Authorization: Bearer dev:<email>
and we either find-or-create the user and their default workspace. This is
just enough to wire the schema and make multi-workspace UI development real,
without committing to an OIDC provider yet (that's Phase 3).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, AsyncIterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .arq_pool import get_arq_pool
from .db.models import User, Workspace
from .db.session import get_session

log = logging.getLogger(__name__)


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    authorization: str | None = Header(default=None),
) -> User:
    """Find-or-create a user for the dev token. Phase 3 swaps for real OIDC."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = authorization.split(" ", 1)[1].strip()
    if not token.startswith("dev:"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phase 1 only accepts dev tokens of the form 'dev:<email>'",
        )
    email = token[len("dev:"):].strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email in token")

    row = await session.execute(select(User).where(User.email == email))
    user = row.scalar_one_or_none()
    if user is None:
        user = User(email=email, display_name=email.split("@", 1)[0])
        session.add(user)
        await session.flush()
        ws = Workspace(name="default", owner_id=user.id)
        session.add(ws)
        await session.flush()
        log.info("dev-auth: created user %s and default workspace", email)
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
