from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid

import uvicorn

from app.api.http_app import build_app
from app.logging_setup import configure_logging
from app.roles import SUPPORTED_ROLES, validate_role
from app.services.bootstrap import build_runtime_container


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
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable code reload (dev mode)",
    )
    return parser.parse_args(argv)


def create_runtime_app() -> object:
    role_name = os.getenv("APP_ROLE", "api")
    role = validate_role(role_name)
    run_id = str(uuid.uuid4())
    configure_logging()
    container = build_runtime_container(role)
    return build_app(
        role=role.name,
        run_id=run_id,
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )


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

    container = build_runtime_container(role)
    port = args.port if args.port is not None else _default_port(role.name)
    if args.reload:
        os.environ["APP_ROLE"] = role.name
        uvicorn.run(
            "app.main:create_runtime_app",
            host=args.host,
            port=port,
            log_level="warning",
            reload=True,
            factory=True,
        )
    else:
        app = build_app(
            role=role.name,
            run_id=run_id,
            worker_loop=container.worker_loop,
            api_deps=container.api_deps,
        )
        uvicorn.run(app, host=args.host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
