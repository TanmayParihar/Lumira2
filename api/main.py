"""
Lumira Intelligence Pipeline — FastAPI application.

Startup sequence:
  1. Create PostgreSQL tables (idempotent)
  2. Ensure MinIO buckets exist
  3. Ensure OpenSearch index exists
  4. Mount all routers
"""
from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings

logger = structlog.get_logger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "End-to-end OSINT intelligence pipeline for India. "
            "Ingests RSS, NewsAPI, Serper, GDELT, radio, images, and video; "
            "processes through local LLMs; scores districts; raises proximity alerts."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Startup / shutdown ────────────────────────────────────────────
    @app.on_event("startup")
    async def startup():
        logger.info("lumira.startup", version=settings.app_version)

        # PostgreSQL + PostGIS
        try:
            from storage.database import create_all_tables
            await create_all_tables()
            logger.info("startup.postgres_ok")
        except Exception as e:
            logger.error("startup.postgres_failed", error=str(e))

        # MinIO buckets
        try:
            from storage.minio_client import ensure_buckets
            ensure_buckets()
            logger.info("startup.minio_ok")
        except Exception as e:
            logger.warning("startup.minio_unavailable", error=str(e))

        # OpenSearch index
        try:
            from storage.opensearch_client import ensure_index
            await ensure_index()
            logger.info("startup.opensearch_ok")
        except Exception as e:
            logger.warning("startup.opensearch_unavailable", error=str(e))

        # Media directory
        import os
        os.makedirs(settings.media_base_path, exist_ok=True)

        logger.info("lumira.ready")

    @app.on_event("shutdown")
    async def shutdown():
        from storage.opensearch_client import close as os_close
        await os_close()
        logger.info("lumira.shutdown")

    # ── Routers ───────────────────────────────────────────────────────
    from api.routers.alerts import router as alerts_router
    from api.routers.assets import router as assets_router
    from api.routers.events import router as events_router
    from api.routers.pipeline import router as pipeline_router
    from api.routers.threats import router as threats_router

    app.include_router(events_router)
    app.include_router(threats_router)
    app.include_router(alerts_router)
    app.include_router(assets_router)
    app.include_router(pipeline_router)

    @app.get("/", tags=["root"])
    async def root():
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
        }

    @app.get("/health", tags=["root"])
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
