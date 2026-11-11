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
    namespace: str | None = None,
    external_ref: str | None = None,
    scope_json: str | None = None,
    principal_id: str | None = None,
    policy_json: str | None = None,
) -> Conversation:
    conv = Conversation(
        repository_id=repository_id,
        title=title,
        namespace=namespace,
        external_ref=external_ref,
        scope_json=scope_json,
        principal_id=principal_id,
        policy_json=policy_json,
    )
    session.add(conv)
    await session.flush()
    return conv


async def get_conversation(session: AsyncSession, conversation_id: str) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


async def get_conversation_by_external_ref(
    session: AsyncSession, namespace: str, external_ref: str
) -> Conversation | None:
    """The conversation a caller's thread key names, deleted or not."""
    result = await session.execute(
        select(Conversation).where(
            Conversation.namespace == namespace, Conversation.external_ref == external_ref
        )
    )
    return result.scalars().first()


async def record_conversation_turn(
    session: AsyncSession, conversation_id: str, *, input_tokens: int, output_tokens: int
) -> None:
    """Count one finished turn and the tokens it used."""
    conv = await session.get(Conversation, conversation_id)
    if conv:
        conv.turns = (conv.turns or 0) + 1
        conv.input_tokens = (conv.input_tokens or 0) + input_tokens
        conv.output_tokens = (conv.output_tokens or 0) + output_tokens
        conv.updated_at = _now_utc()
        await session.flush()


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
    idempotency_key: str | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content_json=json.dumps(content),
        idempotency_key=idempotency_key,
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


async def find_idempotent_turn(
    session: AsyncSession, conversation_id: str, idempotency_key: str
) -> tuple[ChatMessage, ChatMessage | None] | None:
    """The user message sent under *idempotency_key* and the reply after it.

    The reply is None when that send never finished (it failed or was
    cancelled before anything was saved).
    """
    result = await session.execute(
        select(ChatMessage).where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.idempotency_key == idempotency_key,
        )
    )
    sent = result.scalars().first()
    if sent is None:
        return None
    messages = await list_chat_messages(session, conversation_id)
    after = messages[[m.id for m in messages].index(sent.id) + 1 :]
    reply = next((m for m in after[:1] if m.role == "assistant"), None)
    return sent, reply


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
