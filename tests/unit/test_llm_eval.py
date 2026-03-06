import pytest
import json
from unittest.mock import AsyncMock, Mock, create_autospec

from app.domain.dto import EvaluateSubmissionCommand, LLMClientResult
from app.domain.evaluation_chain import EvaluationChainSpec
from app.domain.use_cases.llm_eval import evaluate_submission
from app.lib.artifacts.types import NormalizedArtifact
from app.domain.scoring import CriteriaScore


@pytest.fixture
def chain_spec():
    """Создаёт мок EvaluationChainSpec с необходимыми атрибутами."""
    spec = create_autospec(EvaluationChainSpec)

    # Основные поля
    spec.model = "gpt-4"
    spec.chain_version = "test:v1"
    spec.spec_version = "chain-spec:v1"

    # runtime
    runtime = Mock()
    runtime.temperature = 0.5
    runtime.seed = 42
    runtime.response_language = "ru"
    spec.runtime = runtime

    # prompts
    prompts = Mock()
    prompts.system = "System prompt"
    prompts.user_template = "User prompt: {{assignment.title}}"
    spec.prompts = prompts

    # rubric
    rubric = Mock()
    criterion1 = Mock(id="correctness", weight=0.5)
    criterion2 = Mock(id="completeness", weight=0.5)
    rubric.criteria = [criterion1, criterion2]
    ai_policy = Mock()
    ai_policy.require_fields = ["likelihood", "confidence", "disclaimer"]
    rubric.ai_assistance_policy = ai_policy
    spec.rubric = rubric

    # llm_response schema
    spec.llm_response = {
        "type": "json",
        "required": ["score_1_10", "criteria", "organizer_feedback", "candidate_feedback", "ai_assistance"],
        "properties": {}
    }

    return spec


@pytest.fixture
def normalized_artifact():
    return NormalizedArtifact(
        submission_public_id="sub_123",
        assignment_public_id="asg_123",
        source_type="api_upload",
        content_markdown="Ответ кандидата",
        normalization_metadata={},
        schema_version="normalized:v1",
    )


@pytest.fixture
def mock_llm_client():
    client = AsyncMock()
    return client


@pytest.fixture
def command(chain_spec, normalized_artifact):
    return EvaluateSubmissionCommand(
        submission_id="sub_123",
        normalized_artifact=normalized_artifact,
        assignment_title="Задание 1",
        assignment_description="Описание задания",
        chain_spec=chain_spec,
    )


@pytest.mark.asyncio
async def test_evaluate_success(command, mock_llm_client):
    """Успешный сценарий: LLM возвращает корректный JSON."""
    valid_response = {
        "score_1_10": 8,
        "criteria": [
            {"id": "correctness", "score": 9, "reason": "Всё верно"},
            {"id": "completeness", "score": 7, "reason": "Не хватает деталей"},
        ],
        "organizer_feedback": {
            "strengths": ["Логика"],
            "issues": ["Неполнота"],
            "recommendations": ["Добавить примеры"],
        },
        "candidate_feedback": {
            "summary": "Хорошая работа",
            "what_went_well": ["Логика"],
            "what_to_improve": ["Детали"],
        },
        "ai_assistance": {
            "likelihood": 0.2,
            "confidence": 0.8,
            "disclaimer": "Тестовый дисклеймер",
        },
    }

    mock_llm_client.evaluate.return_value = LLMClientResult(
        raw_text=json.dumps(valid_response),
        raw_json=valid_response,
        tokens_input=100,
        tokens_output=50,
        latency_ms=500,
    )

    result = await evaluate_submission(command, llm=mock_llm_client)

    assert result.score_1_10 == 8
    assert result.model == "gpt-4"
    assert result.chain_version == "test:v1"
    assert result.tokens_input == 100
    assert result.tokens_output == 50
    assert result.latency_ms == 500
    assert result.ai_assistance_likelihood == 0.2
    assert result.ai_assistance_confidence == 0.8
    assert result.reproducibility_subset["chain_version"] == "test:v1"
    assert len(result.criteria_scores_json["items"]) == 2
    assert result.raw_output == json.dumps(valid_response)
    mock_llm_client.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_evaluate_invalid_json(command, mock_llm_client):
    """Невалидный JSON от LLM -> ValueError."""
    mock_llm_client.evaluate.return_value = LLMClientResult(
        raw_text="not a json",
        raw_json=None,
        tokens_input=10,
        tokens_output=5,
        latency_ms=100,
    )

    with pytest.raises(ValueError, match="llm output is not valid JSON"):
        await evaluate_submission(command, llm=mock_llm_client)


@pytest.mark.asyncio
async def test_evaluate_missing_top_level_field(command, mock_llm_client):
    """Отсутствие обязательного поля ai_assistance -> ошибка валидации."""
    invalid_response = {
        "score_1_10": 8,
        "criteria": [],
        "organizer_feedback": {},
        "candidate_feedback": {},
        # ai_assistance отсутствует
    }
    mock_llm_client.evaluate.return_value = LLMClientResult(
        raw_text=json.dumps(invalid_response),
        raw_json=invalid_response,
        tokens_input=10,
        tokens_output=5,
        latency_ms=100,
    )

    with pytest.raises(ValueError, match="ai_assistance"):
        await evaluate_submission(command, llm=mock_llm_client)


@pytest.mark.asyncio
async def test_evaluate_criteria_invalid_id(command, mock_llm_client):
    """Критерий с неизвестным id -> ошибка."""
    invalid_response = {
        "score_1_10": 8,
        "criteria": [
            {"id": "unknown", "score": 5, "reason": "??"}
        ],
        "organizer_feedback": {"strengths": [], "issues": [], "recommendations": []},
        "candidate_feedback": {"summary": "", "what_went_well": [], "what_to_improve": []},
        "ai_assistance": {"likelihood": 0.2, "confidence": 0.8, "disclaimer": "..."},
    }
    mock_llm_client.evaluate.return_value = LLMClientResult(
        raw_text=json.dumps(invalid_response),
        raw_json=invalid_response,
        tokens_input=10,
        tokens_output=5,
        latency_ms=100,
    )

    with pytest.raises(ValueError, match="criteria entry id is invalid"):
        await evaluate_submission(command, llm=mock_llm_client)


@pytest.mark.asyncio
async def test_evaluate_criteria_score_out_of_range(command, mock_llm_client):
    """Оценка критерия вне диапазона 1-10 -> ошибка."""
    invalid_response = {
        "score_1_10": 8,
        "criteria": [
            {"id": "correctness", "score": 11, "reason": "слишком много"}
        ],
        "organizer_feedback": {},
        "candidate_feedback": {},
        "ai_assistance": {"likelihood": 0.2, "confidence": 0.8, "disclaimer": "..."},
    }
    mock_llm_client.evaluate.return_value = LLMClientResult(
        raw_text=json.dumps(invalid_response),
        raw_json=invalid_response,
        tokens_input=10,
        tokens_output=5,
        latency_ms=100,
    )

    with pytest.raises(ValueError, match="score must be integer 1-10"):
        await evaluate_submission(command, llm=mock_llm_client)


@pytest.mark.asyncio
async def test_evaluate_missing_ai_field(command, mock_llm_client):
    """Отсутствие обязательного поля в ai_assistance -> ошибка."""
    invalid_response = {
        "score_1_10": 8,
        "criteria": [],
        "organizer_feedback": {},
        "candidate_feedback": {},
        "ai_assistance": {"likelihood": 0.2, "disclaimer": "..."}  # нет confidence
    }
    mock_llm_client.evaluate.return_value = LLMClientResult(
        raw_text=json.dumps(invalid_response),
        raw_json=invalid_response,
        tokens_input=10,
        tokens_output=5,
        latency_ms=100,
    )

    with pytest.raises(ValueError, match="ai_assistance.confidence"):
        await evaluate_submission(command, llm=mock_llm_client)


@pytest.mark.asyncio
async def test_evaluate_ai_likelihood_out_of_range(command, mock_llm_client):
    """likelihood вне [0,1] -> ошибка."""
    invalid_response = {
        "score_1_10": 8,
        "criteria": [],
        "organizer_feedback": {},
        "candidate_feedback": {},
        "ai_assistance": {"likelihood": 1.5, "confidence": 0.8, "disclaimer": "..."}
    }
    mock_llm_client.evaluate.return_value = LLMClientResult(
        raw_text=json.dumps(invalid_response),
        raw_json=invalid_response,
        tokens_input=10,
        tokens_output=5,
        latency_ms=100,
    )

    with pytest.raises(ValueError, match="ai_assistance.likelihood must be a number between 0 and 1"):
        await evaluate_submission(command, llm=mock_llm_client)