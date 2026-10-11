"""Meta-domain delegations for :class:`SqlIndexStore`.

Each method here is a one-to-one delegation to :mod:`crud`. Split out from
``sql_index_store.py`` to keep every store file under 400 lines.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .._interfaces._meta import MetaIndexStore
from ..models import (
    ChatMessage,
    Conversation,
    GenerationJob,
    Page,
    PageVersion,
    Repository,
    WebhookEvent,
)


class _SqlMetaMixin(MetaIndexStore):
    """Concrete delegations for the meta IndexStore surface."""

    _session: AsyncSession

    async def upsert_repository(
        self,
        *,
        name: str,
        local_path: str,
        url: str = "",
        default_branch: str = "main",
        settings: dict | None = None,
    ) -> Repository:
        return await crud.upsert_repository(
            self._session,
            name=name,
            local_path=local_path,
            url=url,
            default_branch=default_branch,
            settings=settings,
        )

    async def get_repository(self, repo_id: str) -> Repository | None:
        return await crud.get_repository(self._session, repo_id)

    async def get_repository_by_path(self, local_path: str) -> Repository | None:
        return await crud.get_repository_by_path(self._session, local_path)

    async def delete_repository(self, repo_id: str) -> bool:
        return await crud.delete_repository(self._session, repo_id)

    async def list_page_ids(self, repository_id: str) -> list[str]:
        return await crud.list_page_ids(self._session, repository_id)

    async def upsert_generation_job(
        self,
        *,
        repository_id: str,
        status: str = "pending",
        provider_name: str = "",
        model_name: str = "",
        total_pages: int = 0,
        config: dict | None = None,
        job_id: str | None = None,
    ) -> GenerationJob:
        return await crud.upsert_generation_job(
            self._session,
            repository_id=repository_id,
            status=status,
            provider_name=provider_name,
            model_name=model_name,
            total_pages=total_pages,
            config=config,
            job_id=job_id,
        )

    async def get_generation_job(self, job_id: str) -> GenerationJob | None:
        return await crud.get_generation_job(self._session, job_id)

    async def update_job_status(
        self,
        job_id: str,
        status: str,
        *,
        completed_pages: int | None = None,
        failed_pages: int | None = None,
        current_level: int | None = None,
        total_pages: int | None = None,
        error_message: str | None = None,
    ) -> GenerationJob:
        return await crud.update_job_status(
            self._session,
            job_id,
            status,
            completed_pages=completed_pages,
            failed_pages=failed_pages,
            current_level=current_level,
            total_pages=total_pages,
            error_message=error_message,
        )

    async def upsert_page(self, **kwargs: Any) -> Page:
        return await crud.upsert_page(self._session, **kwargs)

    async def upsert_page_from_generated(
        self, generated_page: object, repository_id: str
    ) -> Page:
        return await crud.upsert_page_from_generated(
            self._session, generated_page, repository_id
        )

    async def get_page(self, page_id: str) -> Page | None:
        return await crud.get_page(self._session, page_id)

    async def list_pages(
        self,
        repository_id: str,
        *,
        page_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "updated_at",
        order: str = "desc",
    ) -> list[Page]:
        return await crud.list_pages(
            self._session,
            repository_id,
            page_type=page_type,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            order=order,
        )

    async def get_page_versions(
        self, page_id: str, *, limit: int = 50
    ) -> list[PageVersion]:
        return await crud.get_page_versions(self._session, page_id, limit=limit)

    async def get_stale_pages(self, repository_id: str) -> list[Page]:
        return await crud.get_stale_pages(self._session, repository_id)

    async def load_prior_pages(self, repository_id: str) -> dict[str, Any]:
        return await crud.load_prior_pages(self._session, repository_id)

    async def store_webhook_event(
        self,
        *,
        provider: str,
        event_type: str,
        payload: dict,
        repository_id: str | None = None,
        delivery_id: str = "",
    ) -> WebhookEvent:
        return await crud.store_webhook_event(
            self._session,
            provider=provider,
            event_type=event_type,
            payload=payload,
            repository_id=repository_id,
            delivery_id=delivery_id,
        )

    async def mark_webhook_processed(
        self, event_id: str, *, job_id: str | None = None
    ) -> None:
        await crud.mark_webhook_processed(self._session, event_id, job_id=job_id)

    async def create_conversation(
        self, *, repository_id: str, title: str = "New conversation"
    ) -> Conversation:
        return await crud.create_conversation(
            self._session, repository_id=repository_id, title=title
        )

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        return await crud.get_conversation(self._session, conversation_id)

    async def list_conversations(
        self, repository_id: str, *, limit: int = 50
    ) -> list[Conversation]:
        return await crud.list_conversations(self._session, repository_id, limit=limit)

    async def update_conversation_title(
        self, conversation_id: str, title: str
    ) -> Conversation | None:
        return await crud.update_conversation_title(
            self._session, conversation_id, title
        )

    async def delete_conversation(self, conversation_id: str) -> bool:
        return await crud.delete_conversation(self._session, conversation_id)

    async def touch_conversation(self, conversation_id: str) -> None:
        await crud.touch_conversation(self._session, conversation_id)

    async def create_chat_message(
        self, *, conversation_id: str, role: str, content: dict
    ) -> ChatMessage:
        return await crud.create_chat_message(
            self._session,
            conversation_id=conversation_id,
            role=role,
            content=content,
        )

    async def list_chat_messages(self, conversation_id: str) -> list[ChatMessage]:
        return await crud.list_chat_messages(self._session, conversation_id)

    async def count_chat_messages(self, conversation_id: str) -> int:
        return await crud.count_chat_messages(self._session, conversation_id)
