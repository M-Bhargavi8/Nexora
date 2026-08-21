"""Authentication endpoints: signup, login, logout, and the current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rate_limit import AuthThrottle, RateLimit, SlidingWindowRateLimiter
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    SignupRequest,
    UserResponse,
)
from app.services.auth import (
    EmailAlreadyRegistered,
    authenticate_user,
    create_session,
    create_user,
    normalize_email,
    revoke_session,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

_settings = get_settings()

# Limiters are module-level state, exposed so tests can clear them between
# cases. There are two because they stop different attacks -- see
# app/core/rate_limit.py for why one dimension is not enough.
auth_ip_limiter = SlidingWindowRateLimiter(
    max_attempts=_settings.auth_rate_limit_attempts,
    window_seconds=_settings.auth_rate_limit_window_seconds,
)
auth_account_limiter = SlidingWindowRateLimiter(
    max_attempts=_settings.auth_account_rate_limit_attempts,
    window_seconds=_settings.auth_rate_limit_window_seconds,
)

# Login counts only *failed* credential checks, and clears an account's history
# once its owner actually signs in.
login_throttle = AuthThrottle(auth_ip_limiter, auth_account_limiter, name="login")

# Signup counts every attempt, not just failures: here a *successful* call is
# the thing worth limiting, because the abuse is mass account creation rather
# than password guessing.
_signup_rate_limited = Depends(RateLimit(auth_ip_limiter, name="signup"))


def reset_auth_rate_limits() -> None:
    """Clear every auth limiter. Used by tests to isolate one case from the next."""
    auth_ip_limiter.clear()
    auth_account_limiter.clear()

# Deliberately identical for "no such account" and "wrong password". Telling
# them apart would turn login into a way to discover which emails are
# registered.
_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password",
)


def _set_session_cookie(response: Response, token: str) -> None:
    """Attach the session cookie to a response.

    Each flag matters:

    * `httponly` -- JavaScript cannot read the cookie, so an XSS bug cannot
      steal the session. This is the main reason to prefer a cookie over
      localStorage.
    * `secure` -- the browser only sends it over HTTPS. False in local
      development (plain http://localhost), enforced True in production by a
      settings validator.
    * `samesite` -- "lax" withholds the cookie on cross-site subrequests,
      which is the primary CSRF defence.
    * `max_age` -- matches the session's database expiry, so the browser and
      the server agree on when it ends.
    """
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_lifetime_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    """Remove the session cookie.

    The attributes must match those used when setting it, or the browser
    treats it as a different cookie and quietly leaves the original in place.
    """
    settings = get_settings()
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )


@router.post(
    "/signup",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    dependencies=[_signup_rate_limited],
    responses={409: {"description": "That email already has an account"}},
)
async def signup(
    payload: SignupRequest,
    response: Response,
    db: DbSession,
) -> UserResponse:
    """Register a new user and sign them in immediately."""
    try:
        user = await create_user(db, email=payload.email, password=payload.password)
    except EmailAlreadyRegistered:
        # TRADE-OFF: this response confirms an email is registered, which is
        # a mild account-enumeration leak. The alternative -- always claiming
        # success and confirming by email -- needs an email service we do not
        # have yet, and would leave users unable to tell a typo from a real
        # duplicate. The rate limiter above bounds how fast an attacker can
        # probe. Revisit if email delivery is added.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        ) from None

    token = await create_session(db, user)
    _set_session_cookie(response, token)

    return UserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=UserResponse,
    summary="Sign in",
    responses={
        401: {"description": "Incorrect email or password"},
        429: {"description": "Too many failed attempts"},
    },
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
) -> UserResponse:
    # Normalised so that "Ada@Example.com" and "ada@example.com" share one
    # bucket. The throttle normalises again defensively, but doing it here
    # keeps the key identical to the one the lookup uses.
    account = normalize_email(payload.email)

    # Checked before the attempt and recorded after it, because whether an
    # attempt *counts* is only known once the password has been checked.
    login_throttle.check(request, account)

    user = await authenticate_user(db, email=payload.email, password=payload.password)

    if user is None:
        login_throttle.record_failure(request, account)
        # Log the failure without the email address: authentication logs are
        # widely readable, and a failed attempt often contains a password
        # typed into the email field, or reveals who uses the system.
        logger.warning("Failed login attempt")
        raise _INVALID_CREDENTIALS

    # Clears this account's failure history so a user who mistyped twice and
    # then succeeded does not carry a penalty. The IP history deliberately
    # survives -- see AuthThrottle.record_success.
    login_throttle.record_success(request, account)

    token = await create_session(db, user)
    _set_session_cookie(response, token)

    return UserResponse.model_validate(user)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Sign out",
)
async def logout(request: Request, response: Response, db: DbSession) -> MessageResponse:
    """End the current session.

    Requires no authentication and always reports success. Logging out is
    not a privileged action, and someone holding an invalid cookie should
    still end up logged out rather than stuck with a 401 they cannot clear.
    """
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)

    if token:
        await revoke_session(db, token)

    _clear_session_cookie(response)
    return MessageResponse(message="Signed out")


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the signed-in user",
    responses={401: {"description": "Not authenticated"}},
)
async def read_current_user(user: CurrentUser) -> UserResponse:
    """Return the current user.

    The frontend calls this on load to find out whether the cookie it already
    holds is still valid -- it cannot inspect an httpOnly cookie itself.
    """
    return UserResponse.model_validate(user)
