"""FastAPI application factory for HealTeam.

W1 ships a **skeleton** that:
- Boots a FastAPI app.
- Exposes `GET /healthz` (liveness).
- Exposes `GET /readyz` (readiness — returns 200 even without DB/LLM in W1).
- Mounts stub routers for `sessions`, `agents`, `reports`. Each router
  has at most one placeholder endpoint and is documented as W2 work.

It does NOT:
- Connect to Postgres (the lifespan just logs the DATABASE_URL).
- Call any LLM.
- Implement business logic.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import agents as agents_router
from .routers import cases as cases_router
from .routers import reports as reports_router
from .routers import sessions as sessions_router


# Configure logging from environment.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("healteam.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """W1 lifespan: log the configured LLM/DB env vars (no real connections)."""
    logger.info("HealTeam API starting up")
    logger.info("DATABASE_URL=%s", os.getenv("DATABASE_URL", "(unset)"))
    logger.info("LLM_MODEL=%s", os.getenv("LLM_MODEL", "(unset)"))
    logger.info("OPENAI_BASE_URL=%s", os.getenv("OPENAI_BASE_URL", "(unset)"))
    yield
    logger.info("HealTeam API shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="HealTeam API",
        version="0.1.0",
        description="Family Therapy AI Simulator — backend (W1 foundation)",
        lifespan=lifespan,
    )

    # CORS: allow all origins for dev.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- health endpoints ------------------------------------------------
    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readyz() -> dict[str, str]:
        # W1: always ready. W2: check DB + LLM client.
        return {"status": "ready"}

    # ---- versioned routers ------------------------------------------------
    app.include_router(sessions_router.router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(agents_router.router, prefix="/api/v1/agents", tags=["agents"])
    app.include_router(reports_router.router, prefix="/api/v1/reports", tags=["reports"])
    app.include_router(cases_router.router, prefix="/api/v1/cases", tags=["cases"])

    # ---- static files (serve frontend) ------------------------------------
    from fastapi.staticfiles import StaticFiles
    web_public = Path(__file__).resolve().parents[2] / "web" / "public"
    if web_public.exists():
        app.mount("/", StaticFiles(directory=str(web_public), html=True), name="static")

    return app


# uvicorn entrypoint: `uvicorn app.main:app --reload`
app = create_app()
