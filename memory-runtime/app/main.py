import logging
import time

from fastapi import FastAPI, Request

from app.database import init_database
from app.dependencies import get_app_settings
from app.logging_utils import accepted_request_id, configure_logging, current_request_id, log_event
from app.routers.adapters import router as adapters_router
from app.routers.events import router as events_router
from app.routers.health import router as health_router
from app.routers.mcp import router as mcp_router
from app.routers.namespaces import router as namespaces_router
from app.routers.observability import api_router as observability_api_router
from app.routers.observability import router as observability_router
from app.routers.recall import router as recall_router

request_logger = logging.getLogger("app.http")


def create_app() -> FastAPI:
    settings = get_app_settings()
    configure_logging(level="DEBUG" if settings.debug else "INFO")

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        description="Autonomous memory runtime scaffold for agent systems.",
    )

    @app.middleware("http")
    async def trace_request(request: Request, call_next):
        # One line per API request with the route template (no IDs, no query
        # string, no body), status and duration; health probes only on failure.
        request_id = accepted_request_id(request.headers.get("x-request-id"))
        token = current_request_id.set(request_id)
        started = time.perf_counter()
        status = 500
        error_type = None
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            route = request.scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            # Routers included with a prefix report their path without it.
            prefix = settings.api_prefix
            if template != "unmatched" and prefix and request.url.path.startswith(prefix + "/") \
                    and not template.startswith(prefix + "/"):
                template = prefix + template
            if not (template == "/healthz" and status < 400):
                log_event(
                    request_logger,
                    "http.request",
                    level=logging.ERROR if status >= 500 else logging.INFO,
                    method=request.method,
                    route=template,
                    status=status,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    error_type=error_type,
                )
            current_request_id.reset(token)

    app.include_router(health_router)
    app.include_router(observability_router)
    app.include_router(mcp_router)
    app.include_router(namespaces_router, prefix=settings.api_prefix)
    app.include_router(adapters_router, prefix=settings.api_prefix)
    app.include_router(events_router, prefix=settings.api_prefix)
    app.include_router(observability_api_router, prefix=settings.api_prefix)
    app.include_router(recall_router, prefix=settings.api_prefix)

    if settings.auto_create_tables:
        init_database()

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "status": "ok",
            "docs": "/docs",
        }

    return app


app = create_app()
