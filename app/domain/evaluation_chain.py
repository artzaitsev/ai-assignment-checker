from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml


@dataclass(frozen=True)
class AIAssistancePolicy:
    enabled: bool
    affects_score: bool
    require_fields: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeConfig:
    temperature: float
    seed: int | None


@dataclass(frozen=True)
class PromptsConfig:
    system: str
    user_template: str


@dataclass(frozen=True)
class RubricConfig:
    ai_assistance_policy: AIAssistancePolicy


@dataclass(frozen=True)
class EvaluationChainSpec:
    spec_version: str
    chain_version: str
    runtime: RuntimeConfig
    rubric: RubricConfig
    prompts: PromptsConfig
    llm_response: dict[str, object]


PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")


def load_chain_spec(*, file_path: str | Path) -> EvaluationChainSpec:
    data = yaml.safe_load(Path(file_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("chain spec must be a YAML object")
    return parse_chain_spec(data)


def parse_chain_spec(data: dict[str, object]) -> EvaluationChainSpec:
    spec_version = _required_str(data, "spec_version")
    chain_version = _required_str(data, "chain_version")

    runtime_raw = _required_obj(data, "runtime")
    runtime = RuntimeConfig(
        temperature=_required_float(runtime_raw, "temperature"),
        seed=_optional_int(runtime_raw, "seed"),
    )

    rubric_raw = _required_obj(data, "rubric")
    ai_policy_raw = _required_obj(rubric_raw, "ai_assistance_policy")
    ai_policy = AIAssistancePolicy(
        enabled=_required_bool(ai_policy_raw, "enabled"),
        affects_score=_required_bool(ai_policy_raw, "affects_score"),
        require_fields=tuple(_required_str_list(ai_policy_raw, "require_fields")),
    )

    prompts_raw = _required_obj(data, "prompts")
    prompts = PromptsConfig(
        system=_required_str(prompts_raw, "system"),
        user_template=_required_str(prompts_raw, "user_template"),
    )

    llm_response_raw = _required_obj(data, "llm_response")
    llm_response = dict(llm_response_raw)
    _validate_llm_response_spec(llm_response)

    return EvaluationChainSpec(
        spec_version=spec_version,
        chain_version=chain_version,
        runtime=runtime,
        rubric=RubricConfig(ai_assistance_policy=ai_policy),
        prompts=prompts,
        llm_response=llm_response,
    )


def render_user_prompt(*, template: str, inputs: dict[str, object], spec: EvaluationChainSpec) -> str:
    spec_map = _spec_to_mapping(spec)

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = _lookup_dot_path(inputs, key)
        if value is None:
            value = _lookup_dot_path(spec_map, key)
        if value is None:
            raise ValueError(f"missing placeholder value: {key}")
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=True, sort_keys=True)
        return str(value)

    return PLACEHOLDER_RE.sub(_replace, template)


def validate_llm_response(*, payload: dict[str, object], schema: dict[str, object]) -> None:
    _validate_schema_node(payload, schema, path="$")


def resolved_chain_spec_payload(*, spec: EvaluationChainSpec) -> dict[str, object]:
    return {
        "spec_version": spec.spec_version,
        "chain_version": spec.chain_version,
        "runtime": {
            "temperature": spec.runtime.temperature,
            "seed": spec.runtime.seed,
        },
        "rubric": {
            "ai_assistance_policy": {
                "enabled": spec.rubric.ai_assistance_policy.enabled,
                "affects_score": spec.rubric.ai_assistance_policy.affects_score,
                "require_fields": list(spec.rubric.ai_assistance_policy.require_fields),
            },
        },
        "prompts": {
            "system": spec.prompts.system,
            "user_template": spec.prompts.user_template,
        },
        "llm_response": dict(spec.llm_response),
    }


def chain_spec_digest(*, spec: EvaluationChainSpec) -> str:
    payload = resolved_chain_spec_payload(spec=spec)
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_llm_response_spec(schema: dict[str, object]) -> None:
    if schema.get("type") != "json":
        raise ValueError("llm_response.type must be 'json'")
    if not isinstance(schema.get("required"), list):
        raise ValueError("llm_response.required must be a list")
    if not isinstance(schema.get("properties"), dict):
        raise ValueError("llm_response.properties must be an object")


def _validate_schema_node(value: object, schema: dict[str, object], *, path: str) -> None:
    schema_type = schema.get("type")
    if schema_type == "json":
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected JSON object")
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if not isinstance(required, list) or not isinstance(properties, dict):
            raise ValueError("invalid llm_response schema")
        for field in required:
            if not isinstance(field, str):
                raise ValueError("invalid required field name in llm_response schema")
            if field not in value:
                raise ValueError(f"{path}.{field}: required field is missing")
        for key, field_schema in properties.items():
            if key not in value:
                continue
            if not isinstance(field_schema, dict):
                raise ValueError(f"{path}.{key}: field schema must be object")
            _validate_schema_node(value[key], field_schema, path=f"{path}.{key}")
        return

    if schema_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected object")
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if not isinstance(required, list) or not isinstance(properties, dict):
            raise ValueError(f"{path}: invalid object schema")
        for field in required:
            if not isinstance(field, str):
                raise ValueError(f"{path}: invalid required field")
            if field not in value:
                raise ValueError(f"{path}.{field}: required field is missing")
        for key, field_schema in properties.items():
            if key not in value:
                continue
            if not isinstance(field_schema, dict):
                raise ValueError(f"{path}.{key}: field schema must be object")
            _validate_schema_node(value[key], field_schema, path=f"{path}.{key}")
        return

    if schema_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path}: expected array")
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            raise ValueError(f"{path}: array schema must define items")
        for idx, item in enumerate(value):
            _validate_schema_node(item, item_schema, path=f"{path}[{idx}]")
        return

    if schema_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path}: expected string")
        return

    if schema_type == "integer":
        if not isinstance(value, int):
            raise ValueError(f"{path}: expected integer")
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise ValueError(f"{path}: integer is below minimum")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise ValueError(f"{path}: integer is above maximum")
        return

    if schema_type == "number":
        if not isinstance(value, (int, float)):
            raise ValueError(f"{path}: expected number")
        number_value = float(value)
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and number_value < minimum:
            raise ValueError(f"{path}: number is below minimum")
        if isinstance(maximum, (int, float)) and number_value > maximum:
            raise ValueError(f"{path}: number is above maximum")
        return

    if schema_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path}: expected boolean")
        return

    raise ValueError(f"{path}: unsupported schema type '{schema_type}'")


def _spec_to_mapping(spec: EvaluationChainSpec) -> dict[str, object]:
    payload = resolved_chain_spec_payload(spec=spec)
    return {
        "spec_version": payload["spec_version"],
        "chain_version": payload["chain_version"],
        "runtime": payload["runtime"],
        "rubric": payload["rubric"],
    }


def _lookup_dot_path(data: dict[str, object], path: str) -> object | None:
    current: object = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _required_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required and must be non-empty string")
    return value


def _required_float(data: dict[str, object], key: str) -> float:
    value = data.get(key)
    if not isinstance(value, (int, float)):
        raise ValueError(f"{key} is required and must be number")
    return float(value)


def _optional_int(data: dict[str, object], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"{key} must be integer or null")
    return value


def _required_bool(data: dict[str, object], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} is required and must be boolean")
    return value


def _required_obj(data: dict[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} is required and must be object")
    return value


def _required_list(data: dict[str, object], key: str) -> list[object]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} is required and must be list")
    return value


def _required_str_list(data: dict[str, object], key: str) -> list[str]:
    values = _required_list(data, key)
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must contain non-empty strings")
        result.append(value)
    return result

