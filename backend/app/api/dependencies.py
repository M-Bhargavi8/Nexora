"""Shared FastAPI dependencies.

`get_current_user` is the single gate every protected endpoint passes
through. Having exactly one place that answers "who is making this request?"
is what makes authorization auditable -- there is one function to review, not
one check per route that somebody might forget to write.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User
from app.services.auth import get_user_for_session_token

DbSession = Annotated[AsyncSession, Depends(get_db)]

_NOT_AUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
)


async def get_current_user(request: Request, db: DbSession) -> User:
    """Resolve the session cookie to a user, or reject the request.

    Every failure -- no cookie, unknown token, expired session, deactivated
    account -- produces the identical 401. Distinguishing them would tell an
    attacker whether a token they hold was ever valid.
    """
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)

    if not token:
        raise _NOT_AUTHENTICATED

    user = await get_user_for_session_token(db, token)

    if user is None:
        raise _NOT_AUTHENTICATED

    return user


# Annotate a route parameter with this to require authentication:
#     async def handler(user: CurrentUser) -> ...
CurrentUser = Annotated[User, Depends(get_current_user)]
