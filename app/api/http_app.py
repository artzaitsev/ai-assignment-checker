from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from collections.abc import Awaitable, Callable
import logging

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile

from app.api.handlers.assignments import create_assignment_handler, list_assignments_handler
from app.api.handlers.candidates import create_candidate_handler
from app.api.handlers.deps import ApiDeps
from app.api.handlers.exports import export_results_handler
from app.api.handlers.feedback import list_feedback_handler
from app.api.handlers.pipeline import run_test_pipeline_handler
from app.api.handlers.status import get_submission_status_handler, get_submission_status_with_trace_handler
from app.api.handlers.submissions import create_submission_with_candidate_handler, create_submission_with_file_handler
from app.api.schemas import (
    ASSIGNMENT_ID_PATTERN,
    CANDIDATE_ID_PATTERN,
    AssignmentResponse,
    CandidateResponse,
    CreateAssignmentRequest,
    CreateCandidateRequest,
    CreateSubmissionRequest,
    CreateSubmissionResponse,
    ErrorResponse,
    ExportResultsRequest,
    ExportResultsResponse,
    FeedbackListResponse,
    HealthResponse,
    ListAssignmentsResponse,
    ReadyResponse,
    RunPipelineResponse,
    SubmissionStatusResponse,
    UploadSubmissionFileResponse,
    WorkerMetrics,
)
from app.domain.errors import DomainInvariantError

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
    on_startup: Callable[[], Awaitable[None]] | None = None,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
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

        if on_startup is not None:
            await on_startup()

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

        if on_shutdown is not None:
            await on_shutdown()

        logger.info(
            "role stopped",
            extra={"role": role, "service": role, "run_id": run_id},
        )

    app = FastAPI(title="ai-assignment-checker", version="0.1.0", lifespan=lifespan)

    @app.get("/health", response_model=HealthResponse, tags=["System"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", role=role, mode="skeleton")

    @app.get("/ready", response_model=ReadyResponse, tags=["System"])
    async def ready() -> ReadyResponse:
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

        return ReadyResponse(
            status="ready",
            role=role,
            mode="skeleton",
            worker_loop_enabled=worker_loop_enabled,
            worker_loop_ready=worker_loop_ready,
            worker_metrics=WorkerMetrics(
                started=metrics["started"],
                stopped=metrics["stopped"],
                ticks_total=metrics["ticks_total"],
                claims_total=metrics["claims_total"],
                idle_ticks_total=metrics["idle_ticks_total"],
                errors_total=metrics["errors_total"],
            ),
        )

    @app.post(
        "/submissions",
        response_model=CreateSubmissionResponse,
        responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Submissions"],
    )
    async def create_submission(request: CreateSubmissionRequest) -> CreateSubmissionResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        try:
            return await create_submission_with_candidate_handler(
                source_external_id=request.source_external_id,
                candidate_public_id=request.candidate_public_id,
                assignment_public_id=request.assignment_public_id,
                api_deps=api_deps,
            )
        except DomainInvariantError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/submissions/{submission_id}", response_model=SubmissionStatusResponse, tags=["Submissions"])
    async def get_submission_status(submission_id: str) -> SubmissionStatusResponse:
        if api_deps is not None:
            traced_status = await get_submission_status_with_trace_handler(
                submission_id=submission_id,
                api_deps=api_deps,
            )
            if traced_status is not None:
                return traced_status
        return await get_submission_status_handler(submission_id=submission_id)

    @app.post(
        "/submissions/file",
        response_model=UploadSubmissionFileResponse,
        responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Submissions"],
    )
    async def upload_submission_file(
        file: UploadFile = File(...),
        candidate_public_id: str = Form(..., pattern=CANDIDATE_ID_PATTERN),
        assignment_public_id: str = Form(..., pattern=ASSIGNMENT_ID_PATTERN),
    ) -> UploadSubmissionFileResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        file_bytes = await file.read()
        filename = file.filename or "submission.bin"
        try:
            return await create_submission_with_file_handler(
                filename=filename,
                payload=file_bytes,
                candidate_public_id=candidate_public_id,
                assignment_public_id=assignment_public_id,
                api_deps=api_deps,
            )
        except DomainInvariantError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/candidates",
        response_model=CandidateResponse,
        responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Candidates"],
    )
    async def create_candidate(request: CreateCandidateRequest) -> CandidateResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        return await create_candidate_handler(
            first_name=request.first_name,
            last_name=request.last_name,
            source_type=request.source_type,
            source_external_id=request.source_external_id,
            api_deps=api_deps,
        )

    @app.post(
        "/assignments",
        response_model=AssignmentResponse,
        responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Assignments"],
    )
    async def create_assignment(request: CreateAssignmentRequest) -> AssignmentResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        return await create_assignment_handler(
            title=request.title,
            description=request.description,
            is_active=request.is_active,
            api_deps=api_deps,
        )

    @app.get("/assignments", response_model=ListAssignmentsResponse, tags=["Assignments"])
    async def list_assignments(active_only: bool = Query(default=True)) -> ListAssignmentsResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        return await list_assignments_handler(active_only=active_only, api_deps=api_deps)

    @app.get("/feedback", response_model=FeedbackListResponse, tags=["Submissions"])
    async def list_feedback(submission_id: str | None = Query(default=None)) -> FeedbackListResponse:
        return await list_feedback_handler(submission_id=submission_id)

    @app.post(
        "/exports",
        response_model=ExportResultsResponse,
        responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Submissions"],
    )
    async def export_results(request: ExportResultsRequest = Body(...)) -> ExportResultsResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        return await export_results_handler(
            submission_id=request.submission_id,
            feedback_ref=request.feedback_ref,
            storage=api_deps.storage,
        )

    @app.post("/internal/test/run-pipeline", response_model=RunPipelineResponse)
    async def run_test_pipeline(request: dict[str, str] = Body(default={})) -> RunPipelineResponse:  # noqa: B008
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        submission_id = request.get("submission_id")
        if submission_id is None:
            raise HTTPException(status_code=400, detail="submission_id is required")
        pipeline_result = await run_test_pipeline_handler(submission_id=submission_id, api_deps=api_deps)
        if pipeline_result is None:
            raise HTTPException(status_code=404, detail="submission not found")
        return pipeline_result

    return app
