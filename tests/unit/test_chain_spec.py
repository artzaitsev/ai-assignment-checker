from __future__ import annotations

import pytest

from app.domain.evaluation_chain import (
    load_chain_spec,
    parse_chain_spec,
    render_user_prompt,
    validate_llm_response,
)


@pytest.mark.unit
def test_load_default_chain_spec() -> None:
    spec = load_chain_spec(file_path="app/eval/chains/chain.v1.yaml")
    assert spec.spec_version == "chain-spec:v1"
    assert spec.chain_version == "chain:v1"
    assert spec.runtime.response_language == "ru"
    assert len(spec.rubric.criteria) >= 1


@pytest.mark.unit
def test_chain_spec_rejects_non_iso_language() -> None:
    with pytest.raises(ValueError, match="response_language"):
        parse_chain_spec(
            {
                "spec_version": "chain-spec:v1",
                "chain_version": "chain:v1",
                "model": "model:v1",
                "runtime": {"temperature": 0.1, "seed": 42, "response_language": "russian"},
                "rubric": {
                    "criteria": [{"id": "correctness", "description": "d", "weight": 1.0}],
                    "ai_assistance_policy": {
                        "enabled": True,
                        "affects_score": False,
                        "require_fields": ["likelihood", "confidence", "disclaimer"],
                    },
                },
                "prompts": {"system": "s", "user_template": "u"},
                "llm_response": {"type": "json", "required": [], "properties": {}},
            }
        )


@pytest.mark.unit
def test_render_user_prompt_resolves_rubric_and_language() -> None:
    spec = load_chain_spec(file_path="app/eval/chains/chain.v1.yaml")
    rendered = render_user_prompt(
        template=(
            "lang={{runtime.response_language}} rubric={{rubric.criteria}} "
            "submission={{normalized.content_markdown}}"
        ),
        inputs={"normalized": {"content_markdown": "hello"}},
        spec=spec,
    )
    assert "lang=ru" in rendered
    assert '"id": "correctness"' in rendered
    assert "submission=hello" in rendered


@pytest.mark.unit
def test_validate_llm_response_contract() -> None:
    spec = load_chain_spec(file_path="app/eval/chains/chain.v1.yaml")
    payload = {
        "criteria": [{"id": "correctness", "score": 8, "reason": "ok"}],
        "organizer_feedback": {"strengths": ["s"], "issues": ["i"], "recommendations": ["r"]},
        "candidate_feedback": {
            "summary": "ok",
            "what_went_well": ["w"],
            "what_to_improve": ["m"],
        },
        "ai_assistance": {"likelihood": 0.2, "confidence": 0.6, "disclaimer": "d"},
    }
    validate_llm_response(payload=payload, schema=spec.llm_response)
