"""IndexStore mixin: repository, generation jobs, pages, chat, webhooks.

This mixin covers the "meta" surface — the top-level wiki tables that
describe a repository (Repository), the long-running generation jobs that
populate it, the wiki pages themselves, and the conversation/chat history
plus inbound webhook events.

Split out from :class:`IndexStore` to keep each interface file under 400
lines per the project code-quality rule. See ``index_store.py`` for the
aggregate that pulls every mixin together.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import (
    ChatMessage,
    Conversation,
    GenerationJob,
    Page,
    PageVersion,
    Repository,
    WebhookEvent,
)


class MetaIndexStore(ABC):
    """Repository, GenerationJob, Page, Conversation/Chat, Webhook CRUD."""

    # ------------------------------------------------------------------
    # Repository
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_repository(
        self,
        *,
        name: str,
        local_path: str,
        url: str = "",
        default_branch: str = "main",
        settings: dict | None = None,
    ) -> Repository: ...

    @abstractmethod
    async def get_repository(self, repo_id: str) -> Repository | None: ...

    @abstractmethod
    async def get_repository_by_path(self, local_path: str) -> Repository | None: ...

    @abstractmethod
    async def delete_repository(self, repo_id: str) -> bool: ...

    @abstractmethod
    async def list_page_ids(self, repository_id: str) -> list[str]: ...

    # ------------------------------------------------------------------
    # GenerationJob
    # ------------------------------------------------------------------

    @abstractmethod
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
    ) -> GenerationJob: ...

    @abstractmethod
    async def get_generation_job(self, job_id: str) -> GenerationJob | None: ...

    @abstractmethod
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
    ) -> GenerationJob: ...

    # ------------------------------------------------------------------
    # Page
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_page(self, **kwargs: Any) -> Page: ...

    @abstractmethod
    async def upsert_page_from_generated(
        self, generated_page: object, repository_id: str
    ) -> Page: ...

    @abstractmethod
    async def get_page(self, page_id: str) -> Page | None: ...

    @abstractmethod
    async def list_pages(
        self,
        repository_id: str,
        *,
        page_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "updated_at",
        order: str = "desc",
    ) -> list[Page]: ...

    @abstractmethod
    async def get_page_versions(
        self, page_id: str, *, limit: int = 50
    ) -> list[PageVersion]: ...

    @abstractmethod
    async def get_stale_pages(self, repository_id: str) -> list[Page]: ...

    @abstractmethod
    async def load_prior_pages(self, repository_id: str) -> dict[str, Any]: ...

    # ------------------------------------------------------------------
    # WebhookEvent
    # ------------------------------------------------------------------

    @abstractmethod
    async def store_webhook_event(
        self,
        *,
        provider: str,
        event_type: str,
        payload: dict,
        repository_id: str | None = None,
        delivery_id: str = "",
    ) -> WebhookEvent: ...

    @abstractmethod
    async def mark_webhook_processed(
        self, event_id: str, *, job_id: str | None = None
    ) -> None: ...

    # ------------------------------------------------------------------
    # Conversation / ChatMessage
    # ------------------------------------------------------------------

    @abstractmethod
    async def create_conversation(
        self, *, repository_id: str, title: str = "New conversation"
    ) -> Conversation: ...

    @abstractmethod
    async def get_conversation(
        self, conversation_id: str
    ) -> Conversation | None: ...

    @abstractmethod
    async def list_conversations(
        self, repository_id: str, *, limit: int = 50
    ) -> list[Conversation]: ...

    @abstractmethod
    async def update_conversation_title(
        self, conversation_id: str, title: str
    ) -> Conversation | None: ...

    @abstractmethod
    async def delete_conversation(self, conversation_id: str) -> bool: ...

    @abstractmethod
    async def touch_conversation(self, conversation_id: str) -> None: ...

    @abstractmethod
    async def create_chat_message(
        self, *, conversation_id: str, role: str, content: dict
    ) -> ChatMessage: ...

    @abstractmethod
    async def list_chat_messages(
        self, conversation_id: str
    ) -> list[ChatMessage]: ...

    @abstractmethod
    async def count_chat_messages(self, conversation_id: str) -> int: ...
