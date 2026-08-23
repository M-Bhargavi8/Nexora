"""Request and response schemas for chat.

The validation boundary. Anything arriving from the network is checked here
before a single line of business logic runs -- and before anything is sent to
a model, which is the point at which mistakes start costing money.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# SECURITY/COST: an unbounded prompt is an unbounded bill, and a very large one
# is also a way to exhaust server memory before it ever reaches the provider.
# 32k characters is roughly 8k tokens -- far more than anyone types by hand,
# and still small enough to reject a pasted novel outright rather than
# silently truncating it, which would produce a confidently wrong answer to a
# question the user did not ask.
MAX_MESSAGE_LENGTH = 32_000


class ChatRequest(BaseModel):
    """One user turn.

    `conversation_id` is optional: omitting it starts a new conversation. That
    keeps "send the first message" and "continue a thread" a single endpoint
    rather than two that differ by one field.
    """

    conversation_id: uuid.UUID | None = None
    content: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_LENGTH,
        description="The user's message.",
    )


class RegenerateRequest(BaseModel):
    """Ask for a fresh reply to the last question in a conversation.

    No `content`: regenerating re-uses the question already stored, so the
    client cannot quietly substitute a different one and have it attributed to
    the original turn.
    """

    conversation_id: uuid.UUID


class MessageResponse(BaseModel):
    """A stored message, as returned to its owner."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    position: int
    created_at: datetime
    # Present on assistant turns only. Exposed so the UI can show what a
    # conversation actually cost rather than leaving spend invisible.
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class ConversationResponse(BaseModel):
    """A conversation without its messages, for the sidebar."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetailResponse(ConversationResponse):
    """A conversation together with its full message history."""

    messages: list[MessageResponse]
