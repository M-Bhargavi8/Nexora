"""Which files were added to which conversation.

Uploading a file *in a chat* is a statement: "this conversation is about this
document". General retrieval cannot honour that on its own -- it only returns
passages that clear the similarity floor, and a question like "what is my
email?" shares too little with a resume to clear it, even though the person
plainly means the file they just dropped in.

So the link is recorded, and questions in that conversation always draw on its
files first. Stored rather than kept in the browser so that it survives a
reload, and so regenerate and edit -- which only send a conversation id --
see the same documents as the original question did.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.db.base import Base


class ConversationFile(Base):
    """A file attached to a conversation. Both belong to the same user."""

    __tablename__ = "conversation_files"

    # Composite primary key: a file is attached to a conversation at most once,
    # and attaching it again is a no-op rather than a duplicate row.
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        # Deleting the conversation drops the link, never the file: the file
        # is still in the person's library and may be in other chats.
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        # Deleting the file drops it from every chat it was in.
        ForeignKey("files.id", ondelete="CASCADE"),
        primary_key=True,
        # "Which chats is this file in" is answered on file deletion cascades;
        # the PK's leading column does not cover it.
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<ConversationFile conversation={self.conversation_id} file={self.file_id}>"
