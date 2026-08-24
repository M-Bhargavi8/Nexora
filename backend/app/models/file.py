"""An uploaded file and whatever text we managed to get out of it.

Two names are stored for every upload, and the distinction is the whole
security story of this table:

* `filename` is what the user called it. Untrusted -- it may contain `../`,
  a null byte, a drive letter, or 4KB of Unicode. It is a **label**, shown
  back to its owner, and never used to build a path.
* `storage_key` is what NEXORA called it: a UUID this application generated.
  That, and only that, decides where the bytes live on disk.

Keeping them apart is what makes path traversal structurally impossible rather
than something a validator has to keep catching.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.chunk import FileChunk
    from app.models.user import User

# Long enough for a real document name, short enough that the column is not a
# place to store a megabyte of attacker-chosen text.
FILENAME_MAX_LENGTH = 255


class FileStatus(str, enum.Enum):
    """How far an upload got.

    `READY` does not promise useful text -- a scanned PDF is a valid PDF with
    no extractable characters. It promises the file was stored and parsing
    finished without error. `EXTRACTED_EMPTY` says so honestly instead of
    letting the user think extraction silently failed.
    """

    READY = "ready"
    EXTRACTED_EMPTY = "extracted_empty"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class File(Base):
    """One uploaded file belonging to exactly one user."""

    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        # Deleting the row is not enough -- see `delete_file`, which also
        # removes the bytes. This cascade covers account deletion.
        index=True,
        nullable=False,
    )

    # The user's name for the file. Untrusted; a display label only.
    filename: Mapped[str] = mapped_column(String(FILENAME_MAX_LENGTH), nullable=False)

    # Ours. A UUID hex string; the only thing that ever becomes a path.
    storage_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    # The type NEXORA determined by inspecting the bytes -- never the
    # `Content-Type` the client sent, which is attacker-controlled.
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)

    # BigInteger: a 32-bit column tops out at 2GB, and a size column that
    # silently overflows is worse than one that is simply wide enough.
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[FileStatus] = mapped_column(
        Enum(
            FileStatus,
            # VARCHAR + CHECK rather than a native PostgreSQL type, for the
            # same reasons as Message.role: portable to the SQLite the tests
            # use, and extendable without ALTER TYPE.
            native_enum=False,
            length=32,
            name="file_status",
            create_constraint=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    # The extracted text. Nullable because an image has none.
    #
    # Stored here rather than re-parsed on demand: parsing is CPU-bound and the
    # answer never changes for a given file. Phase 6 chunks and embeds this
    # into `file_chunks`; this column stays as the source of truth those chunks
    # are derived from.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Pages for a PDF, sheets for a spreadsheet, NULL where neither applies.
    # Shown so the user can tell a 2-page extract of a 400-page document from a
    # complete one.
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Why extraction failed, in words safe to show the user. SECURITY: never a
    # raw exception string -- those leak file paths and library internals.
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="files")

    chunks: Mapped[list[FileChunk]] = relationship(
        back_populates="file",
        # Deleting a file removes its chunks and their vectors in the same
        # transaction. This is the payoff for keeping vectors in PostgreSQL
        # rather than a separate vector store: there is no second system that
        # could be left holding searchable copies of a deleted document.
        cascade="all, delete-orphan",
        order_by="FileChunk.position",
    )

    __table_args__ = (
        # Defence in depth: the ORM restricts `status`, but a script writing
        # directly to the database should not be able to store a value the
        # application cannot read back.
        CheckConstraint(
            "status IN ('ready', 'extracted_empty', 'unsupported', 'failed')",
            name="file_status",
        ),
    )

    def __repr__(self) -> str:
        # SECURITY: excludes `filename` and `extracted_text`. reprs end up in
        # logs and tracebacks, and both are user content.
        return f"<File id={self.id} status={self.status.value} size={self.size_bytes}>"
