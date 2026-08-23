"""ORM models.

Importing every model here serves a real purpose: Alembic's autogenerate
compares ``Base.metadata`` against the live database, and a model that was
never imported is absent from that metadata. Alembic would then conclude its
table should not exist and helpfully generate a migration that **drops** it.
"""

from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.session import UserSession
from app.models.user import User

__all__ = ["Conversation", "Message", "MessageRole", "User", "UserSession"]
