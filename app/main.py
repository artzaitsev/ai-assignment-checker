from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid

import uvicorn

from app.http_app import build_app
from app.logging_setup import configure_logging
from app.roles import SUPPORTED_ROLES, validate_role


def _default_port(role: str) -> int:
    if role == "api":
        return 8000
    return 8100


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Runtime entrypoint")
    parser.add_argument("--role", required=True, help="Runtime role")
    parser.add_argument("--host", default=os.getenv("APP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--dry-run-startup",
        action="store_true",
        help="Validate startup and exit",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        role = validate_role(args.role)
    except ValueError as exc:
        supported = ", ".join(SUPPORTED_ROLES)
        sys.stderr.write(f"ERROR: {exc}\n")
        sys.stderr.write(f"Try one of: {supported}\n")
        return 2

    configure_logging()
    run_id = str(uuid.uuid4())
    logger = logging.getLogger("runtime")

    logger.info(
        "runtime initialized",
        extra={"role": role.name, "service": role.name, "run_id": run_id},
    )

    if args.dry_run_startup:
        logger.info(
            "dry-run startup complete",
            extra={"role": role.name, "service": role.name, "run_id": run_id},
        )
        return 0

    port = args.port if args.port is not None else _default_port(role.name)
    app = build_app(role=role.name, run_id=run_id)
    uvicorn.run(app, host=args.host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
