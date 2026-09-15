"""Application factory.

There is no module-level ``app`` here on purpose. Every FastAPI instance is
built by :func:`create_app` from explicit settings, which keeps configuration
out of import time and lets tests construct differently-configured apps in the
same process. The real ASGI entrypoint lives in :mod:`app.asgi`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import health
from app.api import v1 as api_v1
from app.api.rate_limit import SlidingWindowLimiter
from app.cache import DistributedRateLimiter, build_cache, make_cache_check
from app.core.config import Settings, get_settings
from app.core.correlation import CorrelationIdMiddleware, get_correlation_id
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.core.metrics import MetricsRegistry
from app.core.readiness import ReadinessRegistry
from app.db.session import create_engine, create_session_factory, make_database_check
from app.providers.circuit import CircuitRegistry
from app.providers.registry import build_providers

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application."""
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application starting",
            extra={
                "environment": settings.environment,
                "dry_run": settings.dry_run,
                "version": __version__,
            },
        )
        if settings.is_production_like and settings.debug:
            logger.warning("debug is enabled in a production-like environment")

        # The engine is owned by the application, not by a module, so that
        # importing anything never opens a connection and shutdown is complete.
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.readiness.register("database", make_database_check(engine))

        # Redis. Registered as a *non-critical* dependency: a cache outage must
        # not withdraw this replica from service, because every cache read has
        # a PostgreSQL fallback and a withdrawn replica serves nobody.
        cache = build_cache(settings)
        app.state.cache = cache
        app.state.readiness.register("redis", make_cache_check(cache), critical=False)

        # Distributed limits sit in front of the per-process ones. Both endpoints
        # behind them are expensive to abuse — signup buys a number, a magic link
        # hands out a credential — so the Redis limiter degrades to the local
        # limiter rather than to no limit at all.
        app.state.signup_rate_limiter = DistributedRateLimiter(
            cache,
            scope="signup",
            limit=settings.signup_rate_limit,
            window_s=settings.signup_rate_limit_window_s,
            fallback=app.state.signup_limiter,
        )
        app.state.login_rate_limiter = DistributedRateLimiter(
            cache,
            scope="login",
            limit=settings.login_rate_limit,
            window_s=settings.login_rate_limit_window_s,
            fallback=app.state.login_limiter,
        )

        # Resolved once. The API only needs providers for the admin actions, but
        # building them here means a misconfigured credential surfaces at
        # startup rather than on the first operator click.
        app.state.providers = build_providers(settings)

        # Vendor circuit breakers. Per process on purpose: a shared breaker in
        # Redis would let a cache problem open every circuit at once, escalating
        # a cache outage into a total vendor outage.
        app.state.circuits = CircuitRegistry(
            failure_threshold=settings.circuit_failure_threshold,
            cooldown_s=settings.circuit_cooldown_s,
        )

        # Connected lazily and tolerantly. A Temporal outage must not stop the
        # API booting: signup still records a tenant, the status page still
        # answers, and only the *starting* of new workflows degrades. Handlers
        # check for None rather than assuming a client.
        app.state.temporal = None
        if settings.uses_temporal:
            try:
                from app.temporal.client import connect

                app.state.temporal = await connect(settings)
            except Exception:
                logger.exception(
                    "could not connect to temporal; provisioning will be degraded",
                    extra={"address": settings.temporal_address},
                )

        try:
            yield
        finally:
            await app.state.providers.aclose()
            await cache.aclose()
            await engine.dispose()
            logger.info("application stopped")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
        # Interactive docs are a development convenience, not a public surface.
        docs_url=None if settings.is_production_like else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production_like else "/openapi.json",
    )

    app.state.settings = settings
    app.state.readiness = ReadinessRegistry()
    # Per-application rather than module-global, so a test's assertions cannot
    # be polluted by counts another test left behind.
    app.state.metrics = MetricsRegistry()
    # Per-process, which is all a single-replica POC needs. Every accepted
    # signup eventually spends money, so the form is never left ungated.
    app.state.signup_limiter = SlidingWindowLimiter(
        limit=settings.signup_rate_limit, window_s=settings.signup_rate_limit_window_s
    )
    # Login attempts are limited separately and far more tightly. A magic-link
    # request costs an email and hands out a credential, so an unthrottled form
    # is both a spam relay and a brute-force surface. Keyed on address+source
    # (see app.api.v1.auth) so one attacker cannot lock a real customer out.
    app.state.login_limiter = SlidingWindowLimiter(
        limit=settings.login_rate_limit, window_s=settings.login_rate_limit_window_s
    )

    # Outermost middleware wins the response header, so correlation is added
    # after CORS to ensure every response carries an id.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[settings.correlation_id_header],
    )
    app.add_middleware(CorrelationIdMiddleware, header_name=settings.correlation_id_header)

    app.include_router(health.router)
    app.include_router(api_v1.router, prefix=settings.api_v1_prefix)

    _register_exception_handlers(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "request failed",
            extra={
                "code": exc.code,
                "retryable": exc.retryable,
                "path": request.url.path,
                **exc.details,
            },
        )
        return JSONResponse(status_code=exc.http_status, content=_envelope(exc.to_dict()))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Give schema rejections the same envelope as everything else.

        FastAPI's default is a bare ``{"detail": [...]}``, so a client would
        otherwise need one parser for validation errors and another for every
        other failure. ``field`` names the first offending field, which is what
        a form needs to highlight it.
        """
        errors = exc.errors()
        first = errors[0] if errors else {}
        location = [str(part) for part in first.get("loc", []) if part != "body"]
        return JSONResponse(
            status_code=422,
            content=_envelope(
                {
                    "code": "invalid_input",
                    "message": str(first.get("msg", "request validation failed")),
                    "retryable": False,
                    "details": {
                        "field": ".".join(location) or None,
                        "errors": [
                            {
                                "field": ".".join(
                                    str(part) for part in error.get("loc", []) if part != "body"
                                ),
                                "message": str(error.get("msg", "")),
                            }
                            for error in errors[:10]
                        ],
                    },
                }
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content=_envelope(
                {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                    "retryable": True,
                }
            ),
        )


def _envelope(error: dict[str, Any]) -> dict[str, Any]:
    """Wrap an error so the client always gets the id needed to report it."""
    return {"error": error, "correlation_id": get_correlation_id()}
