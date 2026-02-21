from __future__ import annotations

from app.domain.models import ProcessResult, WorkItemClaim
from app.workers.handlers import deliver, evaluate, ingest_telegram, normalize
from app.workers.handlers.deps import WorkerDeps
from app.workers.loop import ProcessHandler


def build_process_handler(role: str, deps: WorkerDeps) -> ProcessHandler:
    async def _ingest(claim: WorkItemClaim) -> ProcessResult:
        return await ingest_telegram.process_claim(claim, deps)

    async def _normalize(claim: WorkItemClaim) -> ProcessResult:
        return await normalize.process_claim(claim, deps)

    async def _evaluate(claim: WorkItemClaim) -> ProcessResult:
        return await evaluate.process_claim(claim, deps)

    async def _deliver(claim: WorkItemClaim) -> ProcessResult:
        return await deliver.process_claim(claim, deps)

    handlers: dict[str, ProcessHandler] = {
        "worker-ingest-telegram": _ingest,
        "worker-normalize": _normalize,
        "worker-evaluate": _evaluate,
        "worker-deliver": _deliver,
    }
    handler = handlers.get(role)
    if handler is None:
        raise ValueError(f"No worker handler for role '{role}'")
    return handler
