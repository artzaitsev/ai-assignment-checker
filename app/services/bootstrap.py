from __future__ import annotations

from dataclasses import dataclass

from app.api.handlers.deps import ApiDeps
from app.clients.stub import StubLLMClient, StubStorageClient, StubTelegramClient
from app.domain.contracts import LLMClient, StorageClient, TelegramClient, WorkRepository
from app.repositories.stub import InMemoryWorkRepository
from app.roles import RuntimeRole
from app.workers.handlers.deps import WorkerDeps
from app.workers.handlers.factory import build_process_handler
from app.workers.loop import WorkerLoop
from app.workers.roles import ROLE_TO_STAGE


@dataclass
class RuntimeContainer:
    repository: WorkRepository
    storage: StorageClient
    telegram: TelegramClient
    llm: LLMClient
    api_deps: ApiDeps
    worker_loop: WorkerLoop | None


def build_runtime_container(role: RuntimeRole) -> RuntimeContainer:
    repository = InMemoryWorkRepository()
    storage = StubStorageClient()
    telegram = StubTelegramClient()
    llm = StubLLMClient()
    api_deps = ApiDeps(
        repository=repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
        submissions={},
    )

    worker_loop: WorkerLoop | None = None
    if role.name in ROLE_TO_STAGE:
        worker_deps = WorkerDeps(storage=storage, telegram=telegram, llm=llm)
        worker_loop = WorkerLoop(
            role=role.name,
            stage=ROLE_TO_STAGE[role.name],
            repository=repository,
            process=build_process_handler(role.name, worker_deps),
        )

    return RuntimeContainer(
        repository=repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
        api_deps=api_deps,
        worker_loop=worker_loop,
    )
