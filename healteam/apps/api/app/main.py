"""Local durable practice API with offline readiness and safe diagnostics."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .resources import CaseResourceError, check_readiness_resources, project_root
from .routers import agents as agents_router
from .routers import cases as cases_router
from .routers import reviews as reviews_router
from .routers import sessions as sessions_router
from .routers import training as training_router
from .settings import ConfigurationError, load_settings

# Configure logging from environment.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("healteam.api")


def create_app(
    *, database_url: str | None = None, training_delay: float = 0.08, allow_legacy_demo: bool = False
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from healteam_memory.runtime_lease import RuntimeInUseError, RuntimeLease
        from healteam_memory.training_store import TrainingRepository

        from .services.training_manager import TrainingManager

        repository = None
        manager = None
        lease = None
        manager_started = False
        app.state.training_manager = None
        app.state.training_error = None
        try:
            settings = load_settings(require_live_credentials=False)
            url = database_url or settings.database_url or str(project_root() / "data" / "healteam.sqlite3")
            repository = TrainingRepository(
                url,
                busy_timeout_seconds=settings.storage_busy_timeout_seconds,
                write_guard=lambda: lease is not None and lease.acquired,
            )
            lease = RuntimeLease(repository.path)
            lease.acquire()
            manager = TrainingManager(
                repository,
                settings=settings,
                response_delay=training_delay,
                operation_timeout_seconds=settings.operation_timeout_seconds,
                stop_timeout_seconds=settings.stop_timeout_seconds,
            )
            await manager.startup()
            manager_started = True
            app.state.training_manager = manager
        except (ConfigurationError, CaseResourceError) as exc:
            app.state.training_error = exc.code if isinstance(exc, CaseResourceError) else str(exc)
        except RuntimeInUseError:
            app.state.training_error = "runtime_in_use"
        except Exception as exc:
            app.state.training_error = "storage_unavailable"
            logger.error("Local training storage unavailable (%s)", type(exc).__name__)
        logger.info("HealTeam API starting up")
        try:
            yield
        finally:
            try:
                if manager is not None and manager_started:
                    await manager.shutdown()
            finally:
                try:
                    if repository is not None:
                        await repository.dispose()
                finally:
                    if lease is not None:
                        lease.release()
            logger.info("HealTeam API shutting down")

    app = FastAPI(
        title="HealTeam API",
        version="0.6.0",
        description="Local family therapy practice — S5 local delivery and operations",
        lifespan=lifespan,
    )
    # Kept only for isolated regression tests of the S0 adapter. Production
    # callers use the durable demo mode and its single-active-session guard.
    app.state.allow_legacy_demo = allow_legacy_demo
    # Imported here only for registering a typed, safe exception boundary.
    from .services.training_manager import ManagerError

    @app.exception_handler(ManagerError)
    async def training_error_handler(request, exc):
        return JSONResponse(
            status_code=exc.status_code, content={"detail": {"code": exc.code, **exc.details}}
        )

    # Single-user local development origins only; this is not authentication.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    # ---- health endpoints ------------------------------------------------
    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, object]:
        try:
            return {"status": "ok", **load_settings().public_status()}
        except ConfigurationError:
            return {"status": "ok", "configuration": "invalid"}

    @app.get("/readyz", tags=["health"])
    async def readyz():
        status: dict[str, object] = {}
        issues: list[str] = []
        resources_ready = True
        try:
            status = load_settings().public_status()
        except ConfigurationError as exc:
            issues.append(str(exc))
        try:
            check_readiness_resources()
        except CaseResourceError as exc:
            resources_ready = False
            issues.append(exc.code)
        storage_error = getattr(app.state, "training_error", None)
        manager = getattr(app.state, "training_manager", None)
        if manager is not None:
            try:
                await manager.list_sessions(limit=1, offset=0)
            except ManagerError:
                storage_error = "storage_unavailable"
        if storage_error and storage_error not in issues:
            issues.append(storage_error)
        return JSONResponse(
            status_code=503 if issues else 200,
            content={
                "status": "not_ready" if issues else "ready",
                **status,
                "checks": {
                    "configuration": "configured" if status else "invalid",
                    "resources": "available" if resources_ready else "invalid",
                    "storage": "available" if manager is not None and not storage_error else "invalid",
                },
                "issues": issues,
            },
        )

    # ---- versioned routers ------------------------------------------------
    app.include_router(training_router.router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(training_router.reports_router, prefix="/api/v1/reports", tags=["reports"])
    app.include_router(reviews_router.router, prefix="/api/v1/reports", tags=["human reviews"])
    app.include_router(reviews_router.calibration_router, prefix="/api/v1/calibration", tags=["calibration"])
    app.include_router(sessions_router.router, prefix="/api/v1/demo/sessions", tags=["legacy demo"])
    app.include_router(agents_router.router, prefix="/api/v1/agents", tags=["agents"])
    app.include_router(cases_router.router, prefix="/api/v1/cases", tags=["cases"])

    # ---- static files (serve frontend) ------------------------------------
    from fastapi.staticfiles import StaticFiles

    try:
        web_public = project_root() / "apps" / "web" / "public"
        if web_public.exists():
            app.mount("/", StaticFiles(directory=str(web_public), html=True), name="static")
    except CaseResourceError:
        pass  # /healthz and /readyz remain reachable for configuration diagnosis.

    return app


# uvicorn entrypoint: `uvicorn app.main:app --reload`
app = create_app()
