from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
import re
from urllib.parse import quote

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.api.handlers.admin import (
    create_admin_export_handler,
    get_admin_submission_detail_handler,
    list_admin_submissions_handler,
)
from app.api.handlers.admin_assignments import (
    build_assignment_template_download_link,
    create_admin_assignment_handler,
    default_task_schema_json,
    list_admin_assignments_handler,
    parse_admin_assignment_form,
    update_admin_assignment_handler,
)
from app.api.handlers.assignments import create_assignment_handler, delete_assignment_handler, list_assignments_handler
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
from app.domain.telegram_settings import TELEGRAM_DEFAULT_ASSIGNMENT_STREAM
from app.lib.artifacts.refs import storage_key_from_ref
from app.lib.docx import build_assignment_template_docx

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

    def _form_checkbox_to_bool(value: str | None) -> bool:
        return value is not None and value.lower() in {"1", "true", "on", "yes"}

    def _normalized_optional_str(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _parse_score(value: str | None, *, field: str) -> int | None:
        normalized = _normalized_optional_str(value)
        if normalized is None:
            return None
        try:
            parsed = int(normalized)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid {field}") from exc
        if parsed < 1 or parsed > 10:
            raise HTTPException(status_code=400, detail=f"{field} must be between 1 and 10")
        return parsed

    def _safe_docx_filename(stem: str, *, default_stem: str) -> str:
        cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1F]', " ", stem).strip()
        if not cleaned:
            cleaned = default_stem
        cleaned = " ".join(cleaned.split())
        if len(cleaned) > 120:
            cleaned = cleaned[:120].rstrip()
        return f"{cleaned}.docx"

    def _ascii_fallback_docx_filename(file_name: str, *, default_stem: str) -> str:
        stem = file_name[:-5] if file_name.lower().endswith(".docx") else file_name
        ascii_stem = stem.encode("ascii", "ignore").decode("ascii")
        ascii_stem = re.sub(r"[^A-Za-z0-9._ -]", " ", ascii_stem)
        ascii_stem = " ".join(ascii_stem.split()).strip(" .")
        if not ascii_stem:
            ascii_stem = default_stem
        return f"{ascii_stem}.docx"

    async def _get_telegram_default_assignment_id() -> str | None:
        if api_deps is None:
            return None
        value = await api_deps.repository.get_stream_cursor(stream=TELEGRAM_DEFAULT_ASSIGNMENT_STREAM)
        if value is None:
            return None
        value = value.strip()
        return value or None

    async def _set_telegram_default_assignment_id(assignment_public_id: str | None) -> None:
        if api_deps is None:
            return
        await api_deps.repository.set_stream_cursor(
            stream=TELEGRAM_DEFAULT_ASSIGNMENT_STREAM,
            cursor=(assignment_public_id or ""),
        )

    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse(url="/admin/login", status_code=307)

    @app.get("/admin/login", response_class=HTMLResponse, tags=["Admin"])
    async def admin_login_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="admin/login.html",
            context={
                "error_message": None,
                "username_value": "",
            },
        )

    @app.post("/admin/login", response_class=HTMLResponse, tags=["Admin"])
    async def admin_login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        if not username.strip() or not password.strip():
            return templates.TemplateResponse(
                request=request,
                name="admin/login.html",
                context={
                    "error_message": "Введите логин и пароль.",
                    "username_value": username,
                },
                status_code=400,
            )
        response = RedirectResponse(url="/admin/assignments", status_code=303)
        response.set_cookie("admin_session", "demo", httponly=True, samesite="lax")
        return response

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
        submission_id: str | None = None
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
        assignment_hint = session.assignment_hint
        assignment_locked = False
        if assignment_hint is not None:
            hinted_assignment = next(
                (item for item in assignments if item.assignment_public_id == assignment_hint),
                None,
            )
            if hinted_assignment is not None:
                assignments = [hinted_assignment]
                assignment_locked = True
        return templates.TemplateResponse(
            request=request,
            name="candidate_apply/form.html",
            context=form_context(
                assignments=assignments,
                assignment_hint=assignment_hint,
                assignment_locked=assignment_locked,
            ),
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
            if session.assignment_hint is not None and session.assignment_hint != assignment_public_id:
                raise ValueError("Для этой ссылки доступна отправка только по назначенной задаче.")
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

        if submission_id is None:
            return templates.TemplateResponse(
                request=request,
                name="candidate_apply/result.html",
                context=result_context(
                    success=False,
                    title="Не удалось отправить работу",
                    message="Ошибка при сохранении решения.",
                ),
                status_code=500,
            )

        if api_deps.telegram_link_settings is not None:
            result_link = (
                f"{api_deps.telegram_link_settings.public_web_base_url.rstrip('/')}"
                f"/candidate/apply/result/{submission_id}"
            )
            try:
                api_deps.telegram.send_text(
                    chat_id=session.chat_id,
                    message=(
                        "Спасибо! Ваше решение принято. "
                        "Ожидайте обратной связи после проверки.\n\n"
                        f"Страница результата: {result_link}"
                    ),
                )
            except Exception as exc:  # pragma: no cover - ack failure must not break submission acceptance
                logger.warning(
                    "candidate apply accepted but telegram acknowledgement failed",
                    extra={"error": str(exc)},
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

    @app.get("/admin/assignments", response_class=HTMLResponse, tags=["Assignments"])
    async def admin_assignments_page(
        request: Request,
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=25, ge=1, le=100),
    ) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        all_items = await list_admin_assignments_handler(
            api_deps,
            public_base_url=str(request.base_url),
        )
        total_items = len(all_items)
        start = (page - 1) * limit
        end = start + limit
        items = all_items[start:end]
        has_prev = page > 1
        has_next = end < total_items
        return templates.TemplateResponse(
            request=request,
            name="admin/assignments_page.html",
            context={
                "items": items,
                "page": page,
                "limit": limit,
                "has_prev": has_prev,
                "has_next": has_next,
                "page_success": "Задание удалено." if request.query_params.get("deleted") == "1" else None,
                "page_error": None,
            },
        )

    @app.get("/admin/settings", response_class=HTMLResponse, tags=["Admin"])
    async def admin_settings_page(request: Request, saved: int = Query(default=0)) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        assignments = await api_deps.repository.list_assignments(active_only=True)
        return templates.TemplateResponse(
            request=request,
            name="admin/settings_page.html",
            context={
                "assignments": assignments,
                "configured_assignment_id": await _get_telegram_default_assignment_id(),
                "saved": bool(saved),
                "form_error": None,
            },
        )

    @app.post("/admin/settings/telegram-assignment", response_class=HTMLResponse, tags=["Admin"])
    async def admin_settings_update_telegram_assignment(
        request: Request,
        assignment_public_id: str | None = Form(default=None),
    ) -> Response:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        selected = (assignment_public_id or "").strip()
        if selected:
            assignment = await api_deps.repository.get_assignment_by_public_id(
                assignment_public_id=selected,
                include_task_schema=False,
            )
            if assignment is None or not assignment.is_active:
                assignments = await api_deps.repository.list_assignments(active_only=True)
                return templates.TemplateResponse(
                    request=request,
                    name="admin/settings_page.html",
                    context={
                        "assignments": assignments,
                        "configured_assignment_id": await _get_telegram_default_assignment_id(),
                        "saved": False,
                        "form_error": "Выбранная задача не найдена или неактивна.",
                    },
                    status_code=400,
                )

        await _set_telegram_default_assignment_id(selected or None)
        return RedirectResponse(url="/admin/settings?saved=1", status_code=303)

    @app.get("/admin/assignments/new", response_class=HTMLResponse, tags=["Assignments"])
    async def admin_assignment_new_page(request: Request) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        return templates.TemplateResponse(
            request=request,
            name="admin/assignment_form.html",
            context={
                "mode": "create",
                "assignment_public_id": None,
                "form_action": "/admin/assignments",
                "title_value": "",
                "description_value": "",
                "language_value": "ru",
                "is_active_value": True,
                "task_schema_json": default_task_schema_json(),
                "form_error": None,
                "saved": False,
            },
        )

    @app.post("/admin/assignments", response_class=HTMLResponse, tags=["Assignments"])
    async def admin_assignment_create(
        request: Request,
        title: str = Form(...),
        description: str = Form(...),
        language: str = Form(...),
        is_active: str | None = Form(default=None),
        task_schema_json: str = Form(...),
    ) -> Response:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        is_active_value = _form_checkbox_to_bool(is_active)
        try:
            payload = parse_admin_assignment_form(
                title=title,
                description=description,
                language=language,
                is_active=is_active_value,
                task_schema_json=task_schema_json,
            )
            created = await create_admin_assignment_handler(api_deps, payload=payload)
        except (DomainInvariantError, ValueError) as exc:
            return templates.TemplateResponse(
                request=request,
                name="admin/assignment_form.html",
                context={
                    "mode": "create",
                    "assignment_public_id": None,
                    "form_action": "/admin/assignments",
                    "title_value": title,
                    "description_value": description,
                    "language_value": language,
                    "is_active_value": is_active_value,
                    "task_schema_json": task_schema_json,
                    "form_error": str(exc),
                    "saved": False,
                },
                status_code=400,
            )

        return RedirectResponse(
            url=f"/admin/assignments/{created.assignment_public_id}/edit?saved=1",
            status_code=303,
        )

    @app.get("/admin/assignments/{assignment_public_id}/edit", response_class=HTMLResponse, tags=["Assignments"])
    async def admin_assignment_edit_page(
        request: Request,
        assignment_public_id: str,
        saved: int = Query(default=0),
    ) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        assignment = await api_deps.repository.get_assignment_by_public_id(
            assignment_public_id=assignment_public_id,
            include_task_schema=True,
        )
        if assignment is None:
            raise HTTPException(status_code=404, detail="assignment not found")

        task_schema_json = default_task_schema_json()
        if assignment.task_schema is not None:
            task_schema_json = json.dumps(assignment.task_schema.to_dict(), ensure_ascii=False)

        return templates.TemplateResponse(
            request=request,
            name="admin/assignment_form.html",
            context={
                "mode": "edit",
                "assignment_public_id": assignment.assignment_public_id,
                "form_action": f"/admin/assignments/{assignment.assignment_public_id}",
                "title_value": assignment.title,
                "description_value": assignment.description,
                "language_value": assignment.language,
                "is_active_value": assignment.is_active,
                "task_schema_json": task_schema_json,
                "form_error": None,
                "saved": bool(saved),
            },
        )

    @app.post("/admin/assignments/{assignment_public_id}", response_class=HTMLResponse, tags=["Assignments"])
    async def admin_assignment_update(
        request: Request,
        assignment_public_id: str,
        title: str = Form(...),
        description: str = Form(...),
        language: str = Form(...),
        is_active: str | None = Form(default=None),
        task_schema_json: str = Form(...),
    ) -> Response:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        is_active_value = _form_checkbox_to_bool(is_active)
        try:
            payload = parse_admin_assignment_form(
                title=title,
                description=description,
                language=language,
                is_active=is_active_value,
                task_schema_json=task_schema_json,
            )
            updated = await update_admin_assignment_handler(
                api_deps,
                assignment_public_id=assignment_public_id,
                payload=payload,
            )
        except (DomainInvariantError, ValueError) as exc:
            return templates.TemplateResponse(
                request=request,
                name="admin/assignment_form.html",
                context={
                    "mode": "edit",
                    "assignment_public_id": assignment_public_id,
                    "form_action": f"/admin/assignments/{assignment_public_id}",
                    "title_value": title,
                    "description_value": description,
                    "language_value": language,
                    "is_active_value": is_active_value,
                    "task_schema_json": task_schema_json,
                    "form_error": str(exc),
                    "saved": False,
                },
                status_code=400,
            )

        if updated is None:
            raise HTTPException(status_code=404, detail="assignment not found")

        return RedirectResponse(url=f"/admin/assignments/{assignment_public_id}/edit?saved=1", status_code=303)

    @app.post("/admin/assignments/{assignment_public_id}/delete", response_class=HTMLResponse, tags=["Assignments"])
    async def admin_assignment_delete(request: Request, assignment_public_id: str) -> Response:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        try:
            deleted = await delete_assignment_handler(api_deps, assignment_public_id=assignment_public_id)
        except DomainInvariantError as exc:
            del exc
            items = await list_admin_assignments_handler(
                api_deps,
                public_base_url=str(request.base_url),
            )
            return templates.TemplateResponse(
                request=request,
                name="admin/assignments_page.html",
                context={
                    "items": items[:25],
                    "page": 1,
                    "limit": 25,
                    "has_prev": False,
                    "has_next": len(items) > 25,
                    "page_success": None,
                    "page_error": "Нельзя удалить задачу: у нее уже есть связанные решения.",
                },
                status_code=400,
            )

        if not deleted:
            raise HTTPException(status_code=404, detail="assignment not found")

        return RedirectResponse(url="/admin/assignments?deleted=1", status_code=303)

    @app.get("/candidate/assignments/{assignment_public_id}/apply", response_class=HTMLResponse, tags=["Candidates"])
    async def candidate_assignment_apply_page(
        request: Request,
        assignment_public_id: str,
        token: str | None = Query(default=None),
    ) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        assignment = await api_deps.repository.get_assignment_by_public_id(
            assignment_public_id=assignment_public_id,
            include_task_schema=True,
        )
        if assignment is None:
            raise HTTPException(status_code=404, detail="assignment not found")

        response = templates.TemplateResponse(
            request=request,
            name="candidate_assignments/apply_page.html",
            context={
                "assignment": assignment,
                "template_download_url": build_assignment_template_download_link(
                    assignment_public_id=assignment.assignment_public_id
                ),
            },
        )
        if token is None:
            return response
        try:
            apply_session = await exchange_entry_token_for_session(api_deps, entry_token=token)
        except ValueError as exc:
            error_response = templates.TemplateResponse(
                request=request,
                name="candidate_apply/result.html",
                context=result_context(success=False, title="Ссылка недействительна", message=str(exc)),
                status_code=400,
            )
            error_response.delete_cookie("apply_session")
            return error_response

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

    @app.post("/candidate/assignments/{assignment_public_id}/submit", response_class=HTMLResponse, tags=["Candidates"])
    async def candidate_assignment_submit(
        request: Request,
        assignment_public_id: str,
        first_name: str = Form(..., min_length=1, max_length=128),
        last_name: str = Form(..., min_length=1, max_length=128),
        file: UploadFile = File(...),
    ) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        assignment = await api_deps.repository.get_assignment_by_public_id(
            assignment_public_id=assignment_public_id,
            include_task_schema=False,
        )
        if assignment is None:
            raise HTTPException(status_code=404, detail="assignment not found")

        session = None
        session_token = request.cookies.get("apply_session")
        if session_token:
            try:
                session = validate_apply_session(api_deps, session_token=session_token)
            except ValueError:
                session = None

        try:
            payload = await file.read()
            filename = file.filename or "submission.bin"
            if session is not None:
                if session.assignment_hint is not None and session.assignment_hint != assignment_public_id:
                    raise ValueError("Для этой ссылки доступна отправка только по назначенной задаче.")
                submission_id = await submit_candidate_apply_form(
                    api_deps,
                    session=session,
                    first_name=first_name,
                    last_name=last_name,
                    assignment_public_id=assignment_public_id,
                    filename=filename,
                    payload=payload,
                )
                if api_deps.telegram_link_settings is not None:
                    result_link = (
                        f"{api_deps.telegram_link_settings.public_web_base_url.rstrip('/')}"
                        f"/candidate/apply/result/{submission_id}"
                    )
                    api_deps.telegram.send_text(
                        chat_id=session.chat_id,
                        message=(
                            "Спасибо! Ваше решение принято. "
                            "Ожидайте обратной связи после проверки.\n\n"
                            f"Страница результата: {result_link}"
                        ),
                    )
            else:
                candidate = await api_deps.repository.create_candidate(
                    first_name=first_name.strip(),
                    last_name=last_name.strip(),
                )
                result = await create_submission_with_file_handler(
                    api_deps,
                    filename=filename,
                    payload=payload,
                    candidate_public_id=candidate.candidate_public_id,
                    assignment_public_id=assignment_public_id,
                    source_external_id=f"fixed-apply-{candidate.candidate_public_id}",
                )
                submission_id = result.submission_id
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
        return response

    @app.get("/candidate/assignments/{assignment_public_id}/template.docx", tags=["Candidates"])
    async def candidate_assignment_template_download(assignment_public_id: str) -> Response:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        assignment = await api_deps.repository.get_assignment_by_public_id(
            assignment_public_id=assignment_public_id,
            include_task_schema=True,
        )
        if assignment is None:
            raise HTTPException(status_code=404, detail="assignment not found")

        payload = build_assignment_template_docx(assignment)
        file_name = _safe_docx_filename(assignment.title, default_stem=assignment_public_id)
        ascii_file_name = _ascii_fallback_docx_filename(file_name, default_stem=assignment_public_id)
        headers = {
            "Content-Disposition": f"attachment; filename=\"{ascii_file_name}\"; filename*=UTF-8''{quote(file_name, safe='')}",
        }
        return Response(
            content=payload,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers=headers,
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
        candidate_query: str | None = Query(default=None),
        assignment_query: str | None = Query(default=None),
        sort_by: str | None = Query(default="created_at"),
        sort_order: str | None = Query(default="desc"),
        score_min: str | None = Query(default=None),
        score_max: str | None = Query(default=None),
        limit: int = Query(default=25, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        parsed_status = _parse_submission_status(status)
        parsed_sort_by = _parse_submission_sort_by(sort_by)
        parsed_sort_order = _parse_sort_order(sort_order)
        parsed_score_min = _parse_score(score_min, field="score_min")
        parsed_score_max = _parse_score(score_max, field="score_max")
        if parsed_score_min is not None and parsed_score_max is not None and parsed_score_min > parsed_score_max:
            raise HTTPException(status_code=400, detail="score_min must be <= score_max")
        items = await list_admin_submissions_handler(
            api_deps,
            status=parsed_status,
            candidate_public_id=None,
            candidate_query=_normalized_optional_str(candidate_query),
            assignment_public_id=None,
            assignment_query=_normalized_optional_str(assignment_query),
            score_min=parsed_score_min,
            score_max=parsed_score_max,
            sort_by=parsed_sort_by,
            sort_order=parsed_sort_order,
            limit=limit + 1,
            offset=offset,
        )
        has_next = len(items) > limit
        if has_next:
            items = items[:limit]
        return templates.TemplateResponse(
            request=request,
            name="admin/submissions_page.html",
            context={
                "items": items,
                "status": status or "",
                "candidate_query": candidate_query or "",
                "assignment_query": assignment_query or "",
                "score_min": score_min or "",
                "score_max": score_max or "",
                "sort_by": parsed_sort_by.value,
                "sort_order": parsed_sort_order.value,
                "limit": limit,
                "offset": offset,
                "has_prev": offset > 0,
                "has_next": has_next,
                "assignments_for_autocomplete": await api_deps.repository.list_assignments(active_only=False),
            },
        )

    @app.get("/admin/submissions/table", response_class=HTMLResponse, tags=["Submissions"])
    async def admin_submissions_table(
        request: Request,
        status: str | None = Query(default=None),
        candidate_query: str | None = Query(default=None),
        assignment_query: str | None = Query(default=None),
        sort_by: str | None = Query(default="created_at"),
        sort_order: str | None = Query(default="desc"),
        score_min: str | None = Query(default=None),
        score_max: str | None = Query(default=None),
        limit: int = Query(default=25, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        parsed_status = _parse_submission_status(status)
        parsed_sort_by = _parse_submission_sort_by(sort_by)
        parsed_sort_order = _parse_sort_order(sort_order)
        parsed_score_min = _parse_score(score_min, field="score_min")
        parsed_score_max = _parse_score(score_max, field="score_max")
        items = await list_admin_submissions_handler(
            api_deps,
            status=parsed_status,
            candidate_public_id=None,
            candidate_query=_normalized_optional_str(candidate_query),
            assignment_public_id=None,
            assignment_query=_normalized_optional_str(assignment_query),
            score_min=parsed_score_min,
            score_max=parsed_score_max,
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
                "candidate_query": candidate_query or "",
                "assignment_query": assignment_query or "",
                "score_min": score_min or "",
                "score_max": score_max or "",
                "sort_by": parsed_sort_by.value,
                "sort_order": parsed_sort_order.value,
                "limit": limit,
                "offset": offset,
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

    @app.get("/admin/submissions/{submission_id}/raw", tags=["Submissions"])
    async def admin_submission_raw_download(submission_id: str) -> Response:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")
        try:
            artifact_ref = await api_deps.repository.get_artifact_ref(item_id=submission_id, stage="raw")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="raw artifact not found") from exc

        try:
            payload = api_deps.storage.get_bytes(key=storage_key_from_ref(artifact_ref))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="raw artifact not found") from exc
        file_name = artifact_ref.rsplit("/", 1)[-1] or f"{submission_id}.bin"
        headers = {"Content-Disposition": f'attachment; filename="{file_name}"'}
        return Response(content=payload, media_type="application/octet-stream", headers=headers)

    @app.post("/admin/submissions/export", response_class=HTMLResponse, tags=["Submissions"])
    async def admin_submissions_export(
        request: Request,
        status: str | None = Form(default=None),
        candidate_query: str | None = Form(default=None),
        assignment_query: str | None = Form(default=None),
        score_min: str | None = Form(default=None),
        score_max: str | None = Form(default=None),
        sort_by: str | None = Form(default="created_at"),
        sort_order: str | None = Form(default="desc"),
        limit: int = Form(default=25),
        offset: int = Form(default=0),
    ) -> HTMLResponse:
        if api_deps is None:
            raise HTTPException(status_code=503, detail="api dependencies are not available")

        parsed_status = _parse_submission_status(status)
        parsed_sort_by = _parse_submission_sort_by(sort_by)
        parsed_sort_order = _parse_sort_order(sort_order)
        parsed_score_min = _parse_score(score_min, field="score_min")
        parsed_score_max = _parse_score(score_max, field="score_max")
        result = await create_admin_export_handler(
            api_deps,
            status=parsed_status,
            candidate_public_id=None,
            candidate_query=_normalized_optional_str(candidate_query),
            assignment_public_id=None,
            assignment_query=_normalized_optional_str(assignment_query),
            score_min=parsed_score_min,
            score_max=parsed_score_max,
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
