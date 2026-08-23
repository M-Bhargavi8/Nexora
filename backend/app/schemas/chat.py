"""Request and response schemas for chat.

The validation boundary. Anything arriving from the network is checked here
before a single line of business logic runs -- and before anything is sent to
a model, which is the point at which mistakes start costing money.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.conversation import TITLE_MAX_LENGTH

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


class EditMessageRequest(BaseModel):
    """Replace one of your own questions and answer it again.

    Both ids are required even though `message_id` alone would identify the
    row. The conversation is what carries ownership, so naming it lets the
    handler authorize against the conversation and then confirm the message
    really belongs to it -- rather than trusting a message id to be
    self-authorizing.
    """

    conversation_id: uuid.UUID
    message_id: uuid.UUID
    content: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_LENGTH,
        description="The replacement text for the question.",
    )


class RenameConversationRequest(BaseModel):
    """A new title for a conversation."""

    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)

    @field_validator("title")
    @classmethod
    def _collapse_whitespace(cls, value: str) -> str:
        """Normalize the title, and reject one that is only whitespace.

        `min_length=1` alone accepts a single space, which stores a title that
        renders as an invisible, unclickable row in the sidebar. Collapsing
        also stops a pasted multi-line block from becoming a title containing
        newlines -- the same rule `derive_title` applies to generated titles,
        so typed and generated titles cannot behave differently.
        """
        collapsed = " ".join(value.split())
        if not collapsed:
            raise ValueError("Title cannot be blank.")
        return collapsed


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


class ConversationPage(BaseModel):
    """One page of the sidebar list.

    `has_more` rather than a total count, because knowing whether another page
    exists is all a "Load more" button needs -- and it is answered by asking
    for one row more than requested, which costs nothing. A `total` would mean
    a second `COUNT(*)` over the whole table on every keystroke of a search.
    """

    items: list[ConversationResponse]
    limit: int
    offset: int
    has_more: bool
