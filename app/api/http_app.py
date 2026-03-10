from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from collections.abc import Awaitable, Callable
import logging

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.api.handlers.admin import (
    create_admin_export_handler,
    get_admin_submission_detail_handler,
    list_admin_submissions_handler,
)
from app.api.handlers.assignments import create_assignment_handler, list_assignments_handler
from app.api.handlers.candidate_apply import (
    exchange_entry_token_for_session,
    submit_candidate_apply_form,
    validate_apply_session,
)
from app.api.handlers.candidates import create_candidate_handler
from app.api.handlers.deps import ApiDeps
from app.api.handlers.exports import export_results_handler
from app.api.handlers.feedback import list_feedback_handler
from app.api.handlers.pipeline import run_test_pipeline_handler
from app.api.handlers.status import get_submission_status_handler
from app.api.handlers.submissions import create_submission_with_candidate_handler, create_submission_with_file_handler
from app.api.views.candidate_apply import (
    form_context,
    page_context,
    result_context,
    result_page_context,
    result_panel_context,
)
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
from app.domain.models import SortOrder, SubmissionSortBy, SubmissionStatus

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
    integration_mode: str = "stub",
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
            extra={
                "role": role,
                "service": role,
                "run_id": run_id,
                "integration_mode": integration_mode,
            },
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
            extra={
                "role": role,
                "service": role,
                "run_id": run_id,
                "integration_mode": integration_mode,
            },
        )

    app = FastAPI(title="ai-assignment-checker", version="0.1.0", lifespan=lifespan)
    templates = Jinja2Templates(directory="app/templates")

    def _parse_submission_status(value: str | None) -> SubmissionStatus | None:
        if value is None or not value.strip():
            return None
        try:
            return SubmissionStatus(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid status filter") from exc

    def _parse_submission_sort_by(value: str | None) -> SubmissionSortBy:
        if value is None or not value.strip():
            return SubmissionSortBy.CREATED_AT
        try:
            return SubmissionSortBy(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid sort_by") from exc

    def _parse_sort_order(value: str | None) -> SortOrder:
        if value is None or not value.strip():
            return SortOrder.DESC
        try:
            return SortOrder(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid sort_order") from exc

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
            "stage_duration_ms_total": {},
            "stage_success_total": {},
            "stage_retry_total": {},
            "stage_terminal_failure_total": {},
            "stage_error_total": {},
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
                    "stage_duration_ms_total": dict(worker_state.stage_duration_ms_total),
                    "stage_success_total": dict(worker_state.stage_success_total),
                    "stage_retry_total": dict(worker_state.stage_retry_total),
                    "stage_terminal_failure_total": dict(worker_state.stage_terminal_failure_total),
                    "stage_error_total": dict(worker_state.stage_error_total),
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
                stage_duration_ms_total=metrics["stage_duration_ms_total"],
                stage_success_total=metrics["stage_success_total"],
                stage_retry_total=metrics["stage_retry_total"],
                stage_terminal_failure_total=metrics["stage_terminal_failure_total"],
                stage_error_total=metrics["stage_error_total"],
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
                api_deps,
                source_external_id=request.source_external_id,
                candidate_public_id=request.candidate_public_id,
                assignment_public_id=request.assignment_public_id,
            )
        except DomainInvariantError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/submissions/{submission_id}", response_model=SubmissionStatusResponse, tags=["Submissions"])
    async def get_submission_status(submission_id: str) -> SubmissionStatusResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        payload = await get_submission_status_handler(
            deps=api_deps,
            submission_id=submission_id,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="submission not found")
        return payload

    @app.get("/candidate/apply", response_class=HTMLResponse, tags=["Candidates"])
    async def candidate_apply_page(request: Request, token: str = Query(..., min_length=8)) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        try:
            apply_session = await exchange_entry_token_for_session(api_deps, entry_token=token)
        except ValueError as exc:
            response = templates.TemplateResponse(
                request=request,
                name="candidate_apply/page.html",
                context=page_context(error_message=str(exc)),
                status_code=400,
            )
            response.delete_cookie("apply_session")
            return response

        response = templates.TemplateResponse(
            request=request,
            name="candidate_apply/page.html",
            context=page_context(),
        )
        response.set_cookie(
            key="apply_session",
            value=apply_session,
            max_age=api_deps.apply_session_settings.ttl_seconds if api_deps.apply_session_settings else 900,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/",
        )
        return response

    @app.get("/candidate/apply/form", response_class=HTMLResponse, tags=["Candidates"])
    async def candidate_apply_form(request: Request) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        session_token = request.cookies.get("apply_session")
        try:
            session = validate_apply_session(api_deps, session_token=session_token)
        except ValueError as exc:
            return templates.TemplateResponse(
                request=request,
                name="candidate_apply/result.html",
                context=result_context(
                    success=False,
                    title="Сессия истекла",
                    message=str(exc),
                ),
                status_code=401,
            )

        assignments = await api_deps.repository.list_assignments(active_only=True)
        return templates.TemplateResponse(
            request=request,
            name="candidate_apply/form.html",
            context=form_context(assignments=assignments, assignment_hint=session.assignment_hint),
        )

    @app.post("/candidate/apply/submit", response_class=HTMLResponse, tags=["Candidates"])
    async def candidate_apply_submit(
        request: Request,
        first_name: str = Form(..., min_length=1, max_length=128),
        last_name: str = Form(..., min_length=1, max_length=128),
        assignment_public_id: str = Form(..., pattern=ASSIGNMENT_ID_PATTERN),
        file: UploadFile = File(...),
    ) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        session_token = request.cookies.get("apply_session")
        try:
            session = validate_apply_session(api_deps, session_token=session_token)
            payload = await file.read()
            filename = file.filename or "submission.bin"
            submission_id = await submit_candidate_apply_form(
                api_deps,
                session=session,
                first_name=first_name,
                last_name=last_name,
                assignment_public_id=assignment_public_id,
                filename=filename,
                payload=payload,
            )
        except (DomainInvariantError, ValueError) as exc:
            return templates.TemplateResponse(
                request=request,
                name="candidate_apply/result.html",
                context=result_context(success=False, title="Не удалось отправить работу", message=str(exc)),
                status_code=400,
            )

        response = templates.TemplateResponse(
            request=request,
            name="candidate_apply/result.html",
            context=result_context(
                success=True,
                title="Работа принята",
                message="Мы получили Ваш файл и поставили его в очередь на проверку.",
                submission_id=submission_id,
            ),
        )
        response.headers["HX-Push-Url"] = f"/candidate/apply/result/{submission_id}"
        response.delete_cookie("apply_session")
        return response

    @app.get("/candidate/apply/result/{submission_id}", response_class=HTMLResponse, tags=["Candidates"])
    async def candidate_apply_result_page(request: Request, submission_id: str) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        status_payload = await get_submission_status_handler(deps=api_deps, submission_id=submission_id)
        if status_payload is None:
            raise HTTPException(status_code=404, detail="submission not found")

        return templates.TemplateResponse(
            request=request,
            name="candidate_apply/result_page.html",
            context=result_page_context(submission_id=submission_id),
        )

    @app.get("/candidate/apply/result/{submission_id}/panel", response_class=HTMLResponse, tags=["Candidates"])
    async def candidate_apply_result_panel(request: Request, submission_id: str) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        status_payload = await get_submission_status_handler(deps=api_deps, submission_id=submission_id)
        if status_payload is None:
            raise HTTPException(status_code=404, detail="submission not found")

        feedback_payload = await list_feedback_handler(deps=api_deps, submission_id=submission_id)
        feedback_item = feedback_payload.items[0] if feedback_payload.items else None
        context = result_panel_context(
            submission_id=submission_id,
            state=status_payload.state,
            feedback_item=feedback_item,
        )
        return templates.TemplateResponse(
            request=request,
            name="candidate_apply/result_panel.html",
            context=context,
        )

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
                api_deps,
                filename=filename,
                payload=file_bytes,
                candidate_public_id=candidate_public_id,
                assignment_public_id=assignment_public_id,
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
            api_deps,
            first_name=request.first_name,
            last_name=request.last_name,
            source_type=request.source_type,
            source_external_id=request.source_external_id,
        )

    @app.post(
        "/assignments",
        response_model=AssignmentResponse,
        response_model_exclude_none=True,
        responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["Assignments"],
    )
    async def create_assignment(request: CreateAssignmentRequest) -> AssignmentResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        return await create_assignment_handler(
            api_deps,
            title=request.title,
            description=request.description,
            language=request.language,
            task_schema=request.task_schema.to_domain(),
            is_active=request.is_active,
        )

    @app.get("/assignments", response_model=ListAssignmentsResponse, response_model_exclude_none=True, tags=["Assignments"])
    async def list_assignments(
        active_only: bool = Query(default=True),
        include_task_schema: bool = Query(default=False),
    ) -> ListAssignmentsResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        return await list_assignments_handler(
            api_deps,
            active_only=active_only,
            include_task_schema=include_task_schema,
        )

    @app.get("/feedback", response_model=FeedbackListResponse, tags=["Submissions"])
    async def list_feedback(submission_id: str | None = Query(default=None)) -> FeedbackListResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        return await list_feedback_handler(deps=api_deps, submission_id=submission_id)

    @app.get("/admin/submissions", response_class=HTMLResponse, tags=["Submissions"])
    async def admin_submissions_page(
        request: Request,
        status: str | None = Query(default=None),
        candidate_public_id: str | None = Query(default=None),
        assignment_public_id: str | None = Query(default=None),
        sort_by: str | None = Query(default="created_at"),
        sort_order: str | None = Query(default="desc"),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        parsed_status = _parse_submission_status(status)
        parsed_sort_by = _parse_submission_sort_by(sort_by)
        parsed_sort_order = _parse_sort_order(sort_order)
        items = await list_admin_submissions_handler(
            api_deps,
            status=parsed_status,
            candidate_public_id=candidate_public_id,
            assignment_public_id=assignment_public_id,
            sort_by=parsed_sort_by,
            sort_order=parsed_sort_order,
            limit=limit,
            offset=offset,
        )
        return templates.TemplateResponse(
            request=request,
            name="admin/submissions_page.html",
            context={
                "items": items,
                "status": status or "",
                "candidate_public_id": candidate_public_id or "",
                "assignment_public_id": assignment_public_id or "",
                "sort_by": parsed_sort_by.value,
                "sort_order": parsed_sort_order.value,
                "limit": limit,
                "offset": offset,
            },
        )

    @app.get("/admin/submissions/table", response_class=HTMLResponse, tags=["Submissions"])
    async def admin_submissions_table(
        request: Request,
        status: str | None = Query(default=None),
        candidate_public_id: str | None = Query(default=None),
        assignment_public_id: str | None = Query(default=None),
        sort_by: str | None = Query(default="created_at"),
        sort_order: str | None = Query(default="desc"),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        parsed_status = _parse_submission_status(status)
        parsed_sort_by = _parse_submission_sort_by(sort_by)
        parsed_sort_order = _parse_sort_order(sort_order)
        items = await list_admin_submissions_handler(
            api_deps,
            status=parsed_status,
            candidate_public_id=candidate_public_id,
            assignment_public_id=assignment_public_id,
            sort_by=parsed_sort_by,
            sort_order=parsed_sort_order,
            limit=limit,
            offset=offset,
        )
        return templates.TemplateResponse(
            request=request,
            name="admin/submissions_table.html",
            context={
                "items": items,
                "status": status or "",
                "candidate_public_id": candidate_public_id or "",
                "assignment_public_id": assignment_public_id or "",
                "sort_by": parsed_sort_by.value,
                "sort_order": parsed_sort_order.value,
            },
        )

    @app.get("/admin/submissions/{submission_id}", response_class=HTMLResponse, tags=["Submissions"])
    async def admin_submission_detail(request: Request, submission_id: str) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        item = await get_admin_submission_detail_handler(api_deps, submission_id=submission_id)
        if item is None:
            raise HTTPException(status_code=404, detail="submission not found")

        return templates.TemplateResponse(
            request=request,
            name="admin/submission_detail.html",
            context={"item": item},
        )

    @app.post("/admin/submissions/export", response_class=HTMLResponse, tags=["Submissions"])
    async def admin_submissions_export(
        request: Request,
        status: str | None = Form(default=None),
        candidate_public_id: str | None = Form(default=None),
        assignment_public_id: str | None = Form(default=None),
        sort_by: str | None = Form(default="created_at"),
        sort_order: str | None = Form(default="desc"),
        limit: int = Form(default=100),
        offset: int = Form(default=0),
    ) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        parsed_status = _parse_submission_status(status)
        parsed_sort_by = _parse_submission_sort_by(sort_by)
        parsed_sort_order = _parse_sort_order(sort_order)
        result = await create_admin_export_handler(
            api_deps,
            status=parsed_status,
            candidate_public_id=candidate_public_id,
            assignment_public_id=assignment_public_id,
            sort_by=parsed_sort_by,
            sort_order=parsed_sort_order,
            limit=max(1, min(limit, 1000)),
            offset=max(0, offset),
        )
        return templates.TemplateResponse(
            request=request,
            name="admin/export_result.html",
            context={
                "export_id": result.export_id,
                "download_url": result.download_url,
                "rows_count": result.rows_count,
            },
        )

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
            api_deps,
            statuses=tuple(request.statuses) if request.statuses else None,
            candidate_public_id=request.candidate_public_id,
            assignment_public_id=request.assignment_public_id,
            source_type=request.source_type,
            sort_by=SubmissionSortBy(request.sort_by),
            sort_order=SortOrder(request.sort_order),
            limit=request.limit,
            offset=request.offset,
        )

    @app.get("/exports/{export_id}/download", tags=["Submissions"])
    async def download_export(export_id: str) -> Response:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        key = f"exports/{export_id}.csv"
        try:
            payload = api_deps.storage.get_bytes(key=key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="export not found") from exc

        headers = {"Content-Disposition": f'attachment; filename="{export_id}.csv"'}
        return Response(content=payload, media_type="text/csv; charset=utf-8", headers=headers)

    # Internal-only helper endpoint for local/dev synthetic end-to-end checks.
    # It executes normalize -> evaluate -> deliver synchronously for a single
    # submission id and returns the in-memory transition trace.
    @app.post("/internal/test/run-pipeline", response_model=RunPipelineResponse)
    async def run_test_pipeline(request: dict[str, str] = Body(default={})) -> RunPipelineResponse:  # noqa: B008
        """Run synthetic pipeline for one submission id.

        Intended for tests and local diagnostics; not part of public API.
        Returns 404 when submission does not exist in current API deps trace.
        """
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        submission_id = request.get("submission_id")
        if submission_id is None:
            raise HTTPException(status_code=400, detail="submission_id is required")
        pipeline_result = await run_test_pipeline_handler(api_deps, submission_id=submission_id)
        if pipeline_result is None:
            raise HTTPException(status_code=404, detail="submission not found")
        return pipeline_result

    return app
