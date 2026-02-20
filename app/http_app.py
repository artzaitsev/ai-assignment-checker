from __future__ import annotations

import logging

from fastapi import FastAPI


def build_app(role: str, run_id: str) -> FastAPI:
    app = FastAPI(title="ai-assignment-checker", version="0.1.0")
    logger = logging.getLogger("runtime")

    @app.on_event("startup")
    async def on_startup() -> None:
        logger.info(
            "role started",
            extra={"role": role, "service": role, "run_id": run_id},
        )

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        logger.info(
            "role stopped",
            extra={"role": role, "service": role, "run_id": run_id},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": role, "mode": "empty"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready", "role": role, "mode": "empty"}

    return app
