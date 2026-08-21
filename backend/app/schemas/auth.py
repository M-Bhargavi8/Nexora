"""Request and response schemas for authentication.

These are the validation boundary: anything arriving from the network is
checked here before a single line of business logic runs. A route handler can
therefore assume its input is well-formed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

# Common passwords that are LONG ENOUGH to pass the minimum-length check.
#
# This distinction matters and is easy to get wrong. A blocklist of "password",
# "qwerty", "letmein" and friends would be pointless here: every one of them is
# shorter than MIN_PASSWORD_LENGTH, so the length rule already rejects them and
# the blocklist would never once fire. It would look like a security control
# while doing nothing.
#
# What actually needs blocking is what people choose *in response to* a length
# requirement -- they pad a weak password until it is long enough. Those are
# the entries below. Attackers know this pattern and try these first.
_COMMON_PASSWORDS = frozenset(
    {
        "password1234",
        "password12345",
        "password123456",
        "passwordpassword",
        "passw0rd1234",
        "123456789012",
        "1234567890123",
        "12345678901234",
        "123456789012345",
        "qwertyuiop12",
        "qwertyuiop123",
        "qwerty123456",
        "qwertyuiopasdf",
        "iloveyou1234",
        "letmein12345",
        "letmein123456",
        "welcome123456",
        "welcome12345",
        "administrator",
        "administrator1",
        "changeme1234",
        "changeme12345",
        "trustno1234567",
        "monkey123456",
        "dragon123456",
        "football1234",
        "baseball1234",
        "sunshine1234",
        "princess1234",
        "starwars1234",
        "superman1234",
        "abcdefghijkl",
        "abcdefghijklm",
        "abcd1234abcd",
        "secretpassword",
        "mypassword123",
        "thisismypassword",
    }
)

# A password built from very few distinct characters ("aaaaaaaaaaaa",
# "abababababab", "123412341234") satisfies a length rule while carrying
# almost no entropy. One general rule catches the whole family, which is far
# more effective than trying to enumerate every variation.
MIN_DISTINCT_CHARACTERS = 5


class SignupRequest(BaseModel):
    """Payload for creating an account."""

    email: EmailStr
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description=f"At least {MIN_PASSWORD_LENGTH} characters.",
    )

    @field_validator("password")
    @classmethod
    def reject_weak_passwords(cls, value: str) -> str:
        """Block passwords that pass the length check but are still trivial.

        Modern guidance (NIST SP 800-63B) favours length plus a blocklist over
        composition rules like "must contain a symbol". Composition rules
        mostly push people toward predictable patterns such as "Password1!",
        which cracking tools try early and humans struggle to remember.
        """
        if value.strip().lower() in _COMMON_PASSWORDS:
            raise ValueError("This password is too common. Please choose another.")

        if len(set(value)) < MIN_DISTINCT_CHARACTERS:
            raise ValueError(
                "This password repeats too few characters. Please choose another."
            )

        return value


class LoginRequest(BaseModel):
    """Payload for signing in.

    Note the deliberate absence of length or complexity validation. Rejecting
    a short password at login with a different error than a wrong password
    would tell an attacker something about the stored value. Login answers
    exactly one question: do these credentials work?
    """

    email: EmailStr
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class UserResponse(BaseModel):
    """A user, as returned to that user.

    `from_attributes` lets FastAPI build this straight from an ORM object.
    Only the fields listed here are serialised, so `password_hash` cannot
    leak into a response even though it exists on the model -- which is
    exactly why schemas are kept separate from models.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    is_active: bool
    created_at: datetime


class MessageResponse(BaseModel):
    """A simple acknowledgement, used by endpoints with nothing to return."""

    message: str
