from __future__ import annotations

import importlib

ulid_module = importlib.import_module("ulid")


def new_submission_public_id() -> str:
    return f"sub_{ulid_module.new().str}"


def new_candidate_public_id() -> str:
    return f"cand_{ulid_module.new().str}"


def new_assignment_public_id() -> str:
    return f"asg_{ulid_module.new().str}"
