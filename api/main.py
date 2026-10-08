import logging
import sys
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from api.observability import observability
from api.observability.config import ObservabilitySettings
from api.services import str_to_bool

# Load environment variables from .env file
load_dotenv()

# Initialize observability (tracing + logging) based on configuration
# This replaces the old init_sentry() and configure_logging() calls
observability.initialize(ObservabilitySettings())

logging.info("Initializing main.py module")

# Log how the server is being started
if len(sys.argv) > 1:
    logging.info(f"Server started with command: {' '.join(sys.argv)}")
    # Parse and log port information from command line args
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            logging.info(f"Server will run on port: {sys.argv[i + 1]}")
        elif arg.startswith("--port="):
            port = arg.split("=", 1)[1]
            logging.info(f"Server will run on port: {port}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    import logging
    import os

    from api.routes.v2_router import get_v2_router
    from cache.background_tasks import background_task_manager
    from cache.prompts_cache import load_all_prompts_to_cache
    from db.db_models import Base
    from db.session import db_engine

    logging.info("Lifespan function called - Starting up app")

    # Log environment port configuration (may be overridden by uvicorn command-line args)
    env_port = os.environ.get("PORT")
    env_host = os.environ.get("HOST")
    if env_port:
        logging.info(f"Environment PORT variable set to: {env_port}")
    if env_host:
        logging.info(f"Environment HOST variable set to: {env_host}")
    logging.info("Note: Actual server port may differ if specified via uvicorn command-line arguments")

    # Create database tables (configurable via environment variable)
    # Set SKIP_DB_TABLE_CHECK=true to skip table check/creation entirely for faster startup
    skip_db_check = str_to_bool(os.getenv("SKIP_DB_TABLE_CHECK", "false"))

    if skip_db_check:
        logging.info("Skipping database table check (SKIP_DB_TABLE_CHECK=true)")
    else:

        async def _ensure_tables_exist():
            """Ensure database tables exist, create only if needed."""
            try:
                from sqlalchemy import inspect

                loop = asyncio.get_event_loop()

                # Check if tables exist (fast query)
                def _check_tables():
                    inspector = inspect(db_engine)
                    existing_tables = set(inspector.get_table_names())
                    required_tables = set(Base.metadata.tables.keys())
                    missing_tables = required_tables - existing_tables
                    return missing_tables

                logging.info("Checking if database tables exist...")
                missing_tables = await loop.run_in_executor(None, _check_tables)

                if missing_tables:
                    logging.info(f"Creating {len(missing_tables)} missing tables: {missing_tables}")
                    await loop.run_in_executor(None, Base.metadata.create_all, db_engine)
                    logging.info("Database tables created successfully")
                else:
                    logging.info("All database tables already exist, skipping creation")

            except Exception as e:
                logging.error(f"Error ensuring database tables exist: {e}")

        # Run table check/creation (fast if tables exist)
        await _ensure_tables_exist()

    # Include V2 router only (V1 routes have been removed)
    logging.info("Including V2 router")
    app.include_router(get_v2_router())
    logging.info("V2 router included successfully")

    # Start background tasks for cache management
    try:
        # Start initial cache loading (non-blocking)
        logging.info("Starting background cache initialization")
        asyncio.create_task(load_all_prompts_to_cache())

        # Start synchronized cache refresh loop (at 0, 15, 30, 45 minutes of each hour)
        from cache.prompts_cache import synchronized_refresh_loop

        logging.info("Starting synchronized cache refresh task (at 0, 15, 30, 45 minutes of each hour)")
        asyncio.create_task(synchronized_refresh_loop())

        logging.info("Background tasks started successfully")

    except Exception as e:
        logging.error(f"Error starting background tasks: {e}")

    yield

    # Cleanup on shutdown
    logging.info("Shutting down app - cleaning up background tasks")
    try:
        await background_task_manager.cancel_all_tasks()
        logging.info("Background tasks cancelled successfully")
    except Exception as e:
        logging.error(f"Error cancelling background tasks: {e}")

    # Shutdown observability providers (flush pending traces/logs)
    try:
        observability.shutdown()
        logging.info("Observability shutdown complete")
    except Exception as e:
        logging.error(f"Error shutting down observability: {e}")

    logging.info("App shutdown complete")


def create_app() -> FastAPI:
    from api.observability.instrumentation import setup_auto_instrumentation
    from api.routes.admin import admin_router
    from api.routes.health import health_router
    from api.routes.usage import usage_router
    from api.settings import api_settings

    logging.info("Creating FastAPI app with lifespan")
    app: FastAPI = FastAPI(
        title=api_settings.title,
        version=api_settings.version,
        docs_url="/docs" if api_settings.docs_enabled else None,
        redoc_url="/redoc" if api_settings.docs_enabled else None,
        openapi_url="/openapi.json" if api_settings.docs_enabled else None,
        lifespan=lifespan,
    )
    logging.info("FastAPI app created with lifespan")

    # Setup OpenTelemetry auto-instrumentation for OTLP backend
    try:
        setup_auto_instrumentation(app, ObservabilitySettings())
    except Exception as e:
        logging.warning(f"Failed to setup auto-instrumentation: {e}")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=api_settings.cors_origin_list,
        allow_origin_regex=r"https://([a-zA-Z0-9-]+\.)?(agno\.com|agno\.dev)",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(admin_router)
    app.include_router(usage_router)

    logging.info("Loaded routes:")
    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            logging.info(f"{','.join(route.methods):10s} {route.path}")

    return app


logging.info("Creating app at module level")
app = create_app()
logging.info("App created at module level")
