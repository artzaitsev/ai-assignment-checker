from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import os

from app.api.handlers.deps import ApiDeps
from app.clients.stub import StubLLMClient, StubStorageClient, StubTelegramClient
from app.domain.contracts import ArtifactRepository, LLMClient, StorageClient, TelegramClient, WorkRepository
from app.lib.artifacts import build_artifact_repository
from app.repositories.postgres import AsyncpgPoolManager, PostgresWorkRepository
from app.repositories.stub import InMemoryWorkRepository
from app.roles import RuntimeRole
from app.workers.handlers.deps import WorkerDeps
from app.workers.handlers.factory import build_process_handler
from app.workers.loop import WorkerLoop
from app.workers.roles import ROLE_TO_STAGE


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


def build_runtime_container(role: RuntimeRole) -> RuntimeContainer:
    database_url = os.getenv("DATABASE_URL")
    on_startup: Callable[[], Awaitable[None]] | None = None
    on_shutdown: Callable[[], Awaitable[None]] | None = None
    if database_url:
        pool_manager = AsyncpgPoolManager(dsn=database_url)
        repository = PostgresWorkRepository(pool_manager=pool_manager)
        on_startup = pool_manager.startup
        on_shutdown = pool_manager.shutdown
    else:
        repository = InMemoryWorkRepository()
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    telegram = StubTelegramClient()
    llm = StubLLMClient()
    api_deps = ApiDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
        submissions={},
    )

    worker_loop: WorkerLoop | None = None
    if role.name in ROLE_TO_STAGE:
        worker_deps = WorkerDeps(
            repository=repository,
            artifact_repository=artifact_repository,
            storage=storage,
            telegram=telegram,
            llm=llm,
        )
        worker_loop = WorkerLoop(
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
