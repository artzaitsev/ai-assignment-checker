from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.api.handlers.deps import ApiDeps
from app.clients.llm import OpenAICompatibleLLMClient
from app.clients.s3 import build_s3_storage_client
from app.clients.stub import StubLLMClient, StubStorageClient, StubTelegramClient
from app.clients.telegram import RealTelegramClient
from app.domain.contracts import ArtifactRepository, LLMClient, StorageClient, TelegramClient, WorkRepository
from app.lib.artifacts import build_artifact_repository
from app.repositories.postgres import AsyncpgPoolManager, PostgresWorkRepository
from app.repositories.stub import InMemoryWorkRepository
from app.roles import RuntimeRole
from app.services.runtime_settings import (
    INTEGRATION_MODE_REAL,
    apply_session_settings_from_env,
    database_settings_from_env,
    integration_mode_from_env,
    llm_settings_from_env,
    s3_settings_from_env,
    telegram_bot_settings_from_env,
    telegram_link_settings_from_env,
)
from app.workers.handlers.deps import WorkerDeps
from app.workers.handlers.factory import build_process_handler
from app.workers.loop import WorkerLoop
from app.workers.roles import ROLE_TO_STAGE
from app.workers.telegram_polling_loop import TelegramPollingWorkerLoop

TELEGRAM_INGEST_SINGLETON_LOCK_KEY = 6_243_911_007


@dataclass
class RuntimeContainer:
    repository: WorkRepository
    artifact_repository: ArtifactRepository
    storage: StorageClient
    telegram: TelegramClient
    llm: LLMClient
    api_deps: ApiDeps
    worker_loop: WorkerLoop | None
    on_startup: Callable[[], Awaitable[None]] | None
    on_shutdown: Callable[[], Awaitable[None]] | None


def build_runtime_container(role: RuntimeRole, *, integration_mode: str | None = None) -> RuntimeContainer:
    resolved_integration_mode = integration_mode or integration_mode_from_env()
    on_startup: Callable[[], Awaitable[None]] | None = None
    on_shutdown: Callable[[], Awaitable[None]] | None = None
    if resolved_integration_mode == INTEGRATION_MODE_REAL:
        database_settings = database_settings_from_env()
        pool_manager = AsyncpgPoolManager(dsn=database_settings.database_url)
        repository = PostgresWorkRepository(pool_manager=pool_manager)
        if role.name == "worker-ingest-telegram":

            async def _on_startup() -> None:
                await pool_manager.startup()
                await repository.ensure_no_null_task_schema_rows()
                await pool_manager.acquire_singleton_lock(lock_key=TELEGRAM_INGEST_SINGLETON_LOCK_KEY)

            on_startup = _on_startup
        else:
            async def _on_startup() -> None:
                await pool_manager.startup()
                await repository.ensure_no_null_task_schema_rows()

            on_startup = _on_startup
        on_shutdown = pool_manager.shutdown
    else:
        repository = InMemoryWorkRepository()

    storage = _build_storage_client(integration_mode=resolved_integration_mode)
    artifact_repository = build_artifact_repository(storage=storage)
    telegram = _build_telegram_client(role=role, integration_mode=resolved_integration_mode)
    llm = _build_llm_client(role=role, integration_mode=resolved_integration_mode)
    telegram_link_settings = telegram_link_settings_from_env()
    apply_session_settings = apply_session_settings_from_env()
    api_deps = ApiDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
        submissions={},
        telegram_link_settings=telegram_link_settings,
        apply_session_settings=apply_session_settings,
    )

    worker_loop: WorkerLoop | None = None
    if role.name in ROLE_TO_STAGE:
        worker_deps = WorkerDeps(
            repository=repository,
            artifact_repository=artifact_repository,
            storage=storage,
            telegram=telegram,
            llm=llm,
            telegram_link_settings=telegram_link_settings,
        )
        loop_cls = TelegramPollingWorkerLoop if role.name == "worker-ingest-telegram" else WorkerLoop
        worker_loop = loop_cls(
            role=role.name,
            stage=ROLE_TO_STAGE[role.name],
            repository=repository,
            process=build_process_handler(role.name, worker_deps),
        )

    return RuntimeContainer(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
        api_deps=api_deps,
        worker_loop=worker_loop,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
    )


def _build_storage_client(*, integration_mode: str) -> StorageClient:
    if integration_mode == INTEGRATION_MODE_REAL:
        s3_settings = s3_settings_from_env()
        return build_s3_storage_client(
            endpoint_url=s3_settings.endpoint_url,
            bucket=s3_settings.bucket,
            access_key_id=s3_settings.access_key_id,
            secret_access_key=s3_settings.secret_access_key,
            region=s3_settings.region,
        )
    return StubStorageClient()


def _build_telegram_client(*, role: RuntimeRole, integration_mode: str) -> TelegramClient:
    if integration_mode == INTEGRATION_MODE_REAL and role.name in {"worker-ingest-telegram", "worker-deliver"}:
        telegram_settings = telegram_bot_settings_from_env()
        return RealTelegramClient(
            bot_token=telegram_settings.bot_token,
            api_base_url=telegram_settings.api_base_url,
        )
    return StubTelegramClient()


def _build_llm_client(*, role: RuntimeRole, integration_mode: str) -> LLMClient:
    if integration_mode == INTEGRATION_MODE_REAL and role.name in {"worker-normalize", "worker-evaluate"}:
        llm_settings = llm_settings_from_env()
        return OpenAICompatibleLLMClient(
            api_key=llm_settings.api_key,
            base_url=llm_settings.base_url,
            model=llm_settings.model,
            request_timeout_seconds=llm_settings.request_timeout_seconds,
            request_max_retries=llm_settings.request_max_retries,
            request_retry_backoff_ms=llm_settings.request_retry_backoff_ms,
        )
    return StubLLMClient()
