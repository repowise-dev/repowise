"""CRUD operations for the chat domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    ChatMessage,
    Conversation,
    _now_utc,
)

# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------


async def create_conversation(
    session: AsyncSession,
    *,
    repository_id: str,
    title: str = "New conversation",
) -> Conversation:
    conv = Conversation(repository_id=repository_id, title=title)
    session.add(conv)
    await session.flush()
    return conv


async def get_conversation(session: AsyncSession, conversation_id: str) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


async def list_conversations(
    session: AsyncSession, repository_id: str, *, limit: int = 50
) -> list[Conversation]:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.repository_id == repository_id, Conversation.deleted_at.is_(None))
        .order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_conversation_title(
    session: AsyncSession, conversation_id: str, title: str
) -> Conversation | None:
    conv = await session.get(Conversation, conversation_id)
    if conv:
        conv.title = title
        conv.updated_at = _now_utc()
        await session.flush()
    return conv


async def delete_conversation(session: AsyncSession, conversation_id: str) -> bool:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        return False
    conv.deleted_at = _now_utc()
    await session.flush()
    return True


async def restore_conversation(session: AsyncSession, conversation_id: str) -> Conversation | None:
    conv = await session.get(Conversation, conversation_id)
    if conv:
        conv.deleted_at = None
        conv.updated_at = _now_utc()
        await session.flush()
    return conv


async def set_conversation_pinned(
    session: AsyncSession, conversation_id: str, pinned: bool
) -> Conversation | None:
    conv = await session.get(Conversation, conversation_id)
    if conv:
        conv.pinned = pinned
        conv.updated_at = _now_utc()
        await session.flush()
    return conv


async def fork_conversation(
    session: AsyncSession,
    conversation_id: str,
    *,
    through_message_id: str | None = None,
    before_message_id: str | None = None,
) -> Conversation | None:
    source = await session.get(Conversation, conversation_id)
    if source is None or source.deleted_at is not None:
        return None
    fork = await create_conversation(
        session,
        repository_id=source.repository_id,
        title=f"Fork of {source.title}",
    )
    for message in await list_chat_messages(session, conversation_id):
        if before_message_id and message.id == before_message_id:
            break
        clone = ChatMessage(
            conversation_id=fork.id,
            role=message.role,
            content_json=message.content_json,
            created_at=message.created_at,
        )
        session.add(clone)
        if through_message_id and message.id == through_message_id:
            break
    await session.flush()
    return fork


async def touch_conversation(session: AsyncSession, conversation_id: str) -> None:
    """Update the updated_at timestamp of a conversation."""
    conv = await session.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = _now_utc()
        await session.flush()


# ---------------------------------------------------------------------------
# ChatMessage CRUD
# ---------------------------------------------------------------------------


async def create_chat_message(
    session: AsyncSession,
    *,
    conversation_id: str,
    role: str,
    content: dict,
) -> ChatMessage:
    msg = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content_json=json.dumps(content),
    )
    session.add(msg)
    await session.flush()
    return msg


async def list_chat_messages(session: AsyncSession, conversation_id: str) -> list[ChatMessage]:
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
    )
    return list(result.scalars().all())


async def update_chat_message_content(
    session: AsyncSession,
    message_id: str,
    content: dict,
) -> ChatMessage | None:
    """Replace one message envelope after an artifact metadata mutation."""
    message = await session.get(ChatMessage, message_id)
    if message is None:
        return None
    message.content_json = json.dumps(content)
    await session.flush()
    return message


async def count_chat_messages(session: AsyncSession, conversation_id: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
    )
    return result.scalar() or 0
