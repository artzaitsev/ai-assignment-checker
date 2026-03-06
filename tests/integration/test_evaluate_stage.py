import pytest
import pytest_asyncio
import json

from app.clients.stub import StubLLMClient
from app.domain.dto import EvaluateSubmissionCommand
from app.domain.evaluation_chain import load_chain_spec
from app.lib.artifacts.types import NormalizedArtifact
from app.lib.artifacts.repository import VersionedArtifactRepository
from app.repositories.stub import InMemoryWorkRepository
from app.domain.use_cases.llm_eval import evaluate_submission


class InMemoryStorage:
    """Простое in-memory хранилище для тестов."""
    def __init__(self):
        self._store = {}

    def put_bytes(self, key: str, payload: bytes) -> str:
        self._store[key] = payload
        return key

    def get_bytes(self, key: str) -> bytes:
        return self._store[key]


@pytest.fixture
def work_repo():
    """Синхронная фикстура, возвращающая экземпляр репозитория."""
    return InMemoryWorkRepository()


@pytest.fixture
def artifact_repo():
    """Фикстура, создающая репозиторий артефактов с in-memory storage."""
    storage = InMemoryStorage()
    return VersionedArtifactRepository(storage=storage, active_contract_version="v1", compat_policy="strict")


@pytest.fixture
def llm_client():
    return StubLLMClient()  # заглушка уже асинхронная


@pytest_asyncio.fixture
async def assignment(work_repo):
    """Асинхронная фикстура, создающая задание."""
    return await work_repo.create_assignment(
        title="Test Assignment",
        description="Description",
        is_active=True,
    )


@pytest_asyncio.fixture
async def candidate(work_repo):
    """Асинхронная фикстура, создающая кандидата."""
    return await work_repo.create_candidate(first_name="John", last_name="Doe")


@pytest_asyncio.fixture
async def submission(work_repo, candidate, assignment):
    """Асинхронная фикстура, создающая сабмишн."""
    result = await work_repo.create_submission_with_source(
        candidate_public_id=candidate.candidate_public_id,
        assignment_public_id=assignment.assignment_public_id,
        source_type="api_upload",
        source_external_id="ext_123",
        initial_status="normalized",
    )
    return result.submission_id


@pytest_asyncio.fixture
async def normalized_artifact_ref(work_repo, artifact_repo, submission, assignment):
    """Асинхронная фикстура, создающая нормализованный артефакт и возвращающая его ref."""
    norm = NormalizedArtifact(
        submission_public_id=submission,
        assignment_public_id=assignment.assignment_public_id,
        source_type="api_upload",
        content_markdown="Candidate answer",
        normalization_metadata={},
        schema_version="normalized:v1",
    )
    ref = artifact_repo.save_normalized(submission_id=submission, artifact=norm)
    await work_repo.link_artifact(
        item_id=submission,
        stage="normalized",
        artifact_ref=ref,
        artifact_version="normalized:v1",
    )
    return ref


@pytest.mark.asyncio
async def test_evaluate_stage_integration(
    work_repo, artifact_repo, llm_client, submission, assignment, normalized_artifact_ref
):
    # 1. Загружаем нормализованный артефакт
    norm_artifact = artifact_repo.load_normalized(artifact_ref=normalized_artifact_ref)

    # 2. Загружаем цепочку (используем версию по умолчанию)
    chain_spec = load_chain_spec(file_path="app/eval/chains/chain.v1.yaml")

    # 3. Выполняем оценку
    cmd = EvaluateSubmissionCommand(
        submission_id=submission,
        normalized_artifact=norm_artifact,
        assignment_title=assignment.title,
        assignment_description=assignment.description,
        chain_spec=chain_spec,
    )
    result = await evaluate_submission(cmd, llm=llm_client)

    # 4. Проверяем, что результат корректен
    assert result.score_1_10 is not None
    assert result.chain_version == chain_spec.chain_version
    assert result.tokens_input > 0
    assert result.raw_output is not None

    # 5. Сохраняем метаданные (имитируем действия обработчика)
    await work_repo.persist_llm_run(
        submission_id=submission,
        provider="stub",
        model=result.model,
        api_base="",
        chain_version=result.chain_version,
        spec_version=chain_spec.spec_version,
        response_language=result.response_language,
        temperature=result.temperature,
        seed=result.seed,
        tokens_input=result.tokens_input,
        tokens_output=result.tokens_output,
        latency_ms=result.latency_ms,
    )

    await work_repo.persist_evaluation(
        submission_id=submission,
        score_1_10=result.score_1_10,
        criteria_scores_json=result.criteria_scores_json,
        organizer_feedback_json=result.organizer_feedback_json,
        candidate_feedback_json=result.candidate_feedback_json,
        ai_assistance_likelihood=result.ai_assistance_likelihood,
        ai_assistance_confidence=result.ai_assistance_confidence,
        reproducibility_subset=result.reproducibility_subset,
    )

    # 6. Проверяем, что записи появились в репозитории
    assert len(work_repo.llm_runs) == 1
    assert len(work_repo.evaluations) == 1
    eval_record = work_repo.evaluations[0]
    assert eval_record["score_1_10"] == result.score_1_10
    assert eval_record["submission_id"] == submission

    llm_record = work_repo.llm_runs[0]
    assert llm_record["chain_version"] == result.chain_version