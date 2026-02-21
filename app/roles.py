from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_ROLES = (
    "api",
    "worker-ingest-telegram",
    "worker-normalize",
    "worker-evaluate",
    "worker-deliver",
)


@dataclass(frozen=True)
class RuntimeRole:
    name: str


def validate_role(role: str) -> RuntimeRole:
    if role in SUPPORTED_ROLES:
        return RuntimeRole(name=role)

    supported = ", ".join(SUPPORTED_ROLES)
    raise ValueError(
        f"Unsupported role '{role}'. Supported roles: {supported}. "
        "Note: migrator is external and not an app role."
    )
