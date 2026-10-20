"""The local ``ConversationStore``: the ``conversations`` table of an index."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.exc import IntegrityError

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.server.chat_seams import StoredConversation, StoredMessage, TurnUsage


def _conversation(row: Any) -> StoredConversation:
    try:
        scope = json.loads(row.scope_json) if row.scope_json else {}
    except json.JSONDecodeError:
        scope = {}
    return StoredConversation(
        id=row.id,
        repository_id=row.repository_id,
        title=row.title,
        scope=scope if isinstance(scope, dict) else {},
        namespace=row.namespace,
        external_ref=row.external_ref,
        principal_id=row.principal_id,
        policy_json=row.policy_json,
        input_tokens=row.input_tokens or 0,
        output_tokens=row.output_tokens or 0,
        turns=row.turns or 0,
        deleted=row.deleted_at is not None,
    )


def _message(row: Any) -> StoredMessage:
    raw = row.content_json
    try:
        content = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        content = {"text": raw}
    return StoredMessage(
        id=row.id, role=row.role, content=content if isinstance(content, dict) else {}
    )


class SqlConversationStore:
    """Conversations in one index database, through its session factory."""

    def __init__(self, session_factory: Any) -> None:
        self._factory = session_factory

    async def get_or_create(
        self,
        *,
        repository_id: str,
        title: str,
        scope: dict[str, Any],
        namespace: str | None,
        external_ref: str | None,
        principal_id: str | None,
        policy_json: str | None,
    ) -> tuple[StoredConversation, bool]:
        if namespace is not None and external_ref is not None:
            found = await self.find(namespace, external_ref)
            if found is not None:
                return found, False
        try:
            async with get_session(self._factory) as session:
                row = await crud.create_conversation(
                    session,
                    repository_id=repository_id,
                    title=title,
                    namespace=namespace,
                    external_ref=external_ref,
                    scope_json=json.dumps(scope),
                    principal_id=principal_id,
                    policy_json=policy_json,
                )
                created = _conversation(row)
        except IntegrityError:
            # A concurrent open of the same thread won the unique key.
            if namespace is None or external_ref is None:
                raise
            found = await self.find(namespace, external_ref)
            if found is None:
                raise
            return found, False
        return created, True

    async def get(self, conversation_id: str) -> StoredConversation | None:
        async with get_session(self._factory) as session:
            row = await crud.get_conversation(session, conversation_id)
            return _conversation(row) if row is not None else None

    async def find(self, namespace: str, external_ref: str) -> StoredConversation | None:
        async with get_session(self._factory) as session:
            row = await crud.get_conversation_by_external_ref(session, namespace, external_ref)
            return _conversation(row) if row is not None else None

    async def messages(self, conversation_id: str) -> list[StoredMessage]:
        async with get_session(self._factory) as session:
            return [_message(m) for m in await crud.list_chat_messages(session, conversation_id)]

    async def find_sent(
        self, conversation_id: str, idempotency_key: str
    ) -> tuple[StoredMessage, StoredMessage | None] | None:
        async with get_session(self._factory) as session:
            found = await crud.find_idempotent_turn(session, conversation_id, idempotency_key)
            if found is None:
                return None
            sent, reply = found
            return _message(sent), (_message(reply) if reply is not None else None)

    async def add_user_message(
        self, conversation_id: str, content: dict[str, Any], *, idempotency_key: str | None
    ) -> str:
        async with get_session(self._factory) as session:
            message = await crud.create_chat_message(
                session,
                conversation_id=conversation_id,
                role="user",
                content=content,
                idempotency_key=idempotency_key,
            )
            return message.id

    async def add_reply(
        self,
        conversation_id: str,
        content: dict[str, Any],
        *,
        title: str | None,
        usage: TurnUsage,
    ) -> str:
        async with get_session(self._factory) as session:
            message = await crud.create_chat_message(
                session, conversation_id=conversation_id, role="assistant", content=content
            )
            await crud.record_conversation_turn(
                session,
                conversation_id,
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
            )
            if title is not None:
                await crud.update_conversation_title(session, conversation_id, title)
            return message.id
