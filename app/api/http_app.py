from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile

from app.api.handlers.deps import ApiDeps
from app.api.handlers.exports import export_results_handler
from app.api.handlers.feedback import list_feedback_handler
from app.api.handlers.pipeline import run_test_pipeline_handler
from app.api.handlers.status import get_submission_status_handler, get_submission_status_with_trace_handler
from app.api.handlers.submissions import create_submission_handler, create_submission_with_file_handler

from app.workers.loop import WorkerLoop
from app.workers.runner import (
    WorkerRuntimeSettings,
    WorkerRuntimeState,
    run_worker_until_stopped,
    worker_runtime_settings_from_env,
)


def build_app(
    role: str,
    run_id: str,
    worker_loop: WorkerLoop | None = None,
    worker_runtime_settings: WorkerRuntimeSettings | None = None,
    api_deps: ApiDeps | None = None,
) -> FastAPI:
    logger = logging.getLogger("runtime")
    worker_state: WorkerRuntimeState | None = None
    worker_task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal worker_task, worker_state
        del app
        stop_event: asyncio.Event | None = None

        logger.info(
            "role started",
            extra={"role": role, "service": role, "run_id": run_id},
        )

        if worker_loop is not None:
            settings = worker_runtime_settings or worker_runtime_settings_from_env()
            worker_state = WorkerRuntimeState()
            stop_event = asyncio.Event()
            worker_task = asyncio.create_task(
                run_worker_until_stopped(
                    worker_loop=worker_loop,
                    role=role,
                    run_id=run_id,
                    stop_event=stop_event,
                    settings=settings,
                    logger=logger,
                    state=worker_state,
                )
            )

        yield

        if stop_event is not None and worker_task is not None:
            stop_event.set()
            await worker_task

        logger.info(
            "role stopped",
            extra={"role": role, "service": role, "run_id": run_id},
        )

    app = FastAPI(title="ai-assignment-checker", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": role, "mode": "skeleton"}

    @app.get("/ready")
    async def ready() -> dict[str, object]:
        worker_loop_enabled = worker_loop is not None
        worker_loop_ready = True
        metrics = {
            "started": False,
            "stopped": False,
            "ticks_total": 0,
            "claims_total": 0,
            "idle_ticks_total": 0,
            "errors_total": 0,
        }
        if worker_loop_enabled:
            worker_loop_ready = (
                worker_state is not None
                and worker_state.started
                and worker_task is not None
                and not worker_task.done()
            )
            if worker_state is not None:
                metrics = {
                    "started": worker_state.started,
                    "stopped": worker_state.stopped,
                    "ticks_total": worker_state.ticks_total,
                    "claims_total": worker_state.claims_total,
                    "idle_ticks_total": worker_state.idle_ticks_total,
                    "errors_total": worker_state.errors_total,
                }

        return {
            "status": "ready",
            "role": role,
            "mode": "skeleton",
            "worker_loop_enabled": worker_loop_enabled,
            "worker_loop_ready": worker_loop_ready,
            "worker_metrics": metrics,
        }

    @app.post("/submissions")
    async def create_submission(payload: dict[str, str] = Body(default={})):  # noqa: B008
        source_external_id = payload.get("source_external_id", "skeleton")
        return await create_submission_handler(source_external_id=source_external_id)

    @app.get("/submissions/{submission_id}")
    async def get_submission_status(submission_id: str) -> dict[str, object]:
        if api_deps is not None:
            traced = await get_submission_status_with_trace_handler(
                submission_id=submission_id,
                api_deps=api_deps,
            )
            if traced is not None:
                return traced
        return await get_submission_status_handler(submission_id=submission_id)

    @app.post("/submissions/file")
    async def upload_submission_file(file: UploadFile = File(...)) -> dict[str, object]:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        payload = await file.read()
        filename = file.filename or "submission.bin"
        return await create_submission_with_file_handler(
            filename=filename,
            payload=payload,
            api_deps=api_deps,
        )

    @app.get("/feedback")
    async def list_feedback(submission_id: str | None = Query(default=None)) -> dict[str, object]:
        return await list_feedback_handler(submission_id=submission_id)

    @app.post("/exports")
    async def export_results(payload: dict[str, str] = Body(default={})):  # noqa: B008
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        submission_id = payload.get("submission_id", "skeleton")
        feedback_ref = payload.get("feedback_ref", f"feedback/{submission_id}.json")
        return await export_results_handler(
            submission_id=submission_id,
            feedback_ref=feedback_ref,
            storage=api_deps.storage,
        )

    @app.post("/internal/test/run-pipeline")
    async def run_test_pipeline(payload: dict[str, str] = Body(default={})):  # noqa: B008
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        submission_id = payload.get("submission_id")
        if submission_id is None:
            raise HTTPException(status_code=400, detail="submission_id is required")
        result = await run_test_pipeline_handler(submission_id=submission_id, api_deps=api_deps)
        if result is None:
            raise HTTPException(status_code=404, detail="submission not found")
        return result

    return app
