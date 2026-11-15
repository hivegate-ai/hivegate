import logging


def configure_logging() -> None:
    """Legacy function for backward compatibility.

    Actual logging configuration is now handled by the ObservabilityManager
    in api/observability/__init__.py. This function is kept for any code
    that calls it directly, but the observability module handles initialization
    in main.py.

    The new observability module supports multiple logging backends:
    - Console (default)
    - OTLP (OpenTelemetry Collector)
    - Logtail/Better Stack (backward compatible)

    Configure via environment variables:
    - OTEL_LOGGING_BACKEND: comma-separated list (console, otlp, logtail)
    - BETTERSTACK_SOURCE_TOKEN: for Logtail backend
    - BETTERSTACK_HOST: optional Logtail host

    See api/observability/config.py for full configuration options.
    """
    from api.observability import observability
    from api.observability.config import ObservabilitySettings

    # If observability is already initialized, skip
    if observability.is_initialized:
        logging.debug("Logging already configured via observability module")
        return

    # Fallback: Initialize observability if not already done
    observability.initialize(ObservabilitySettings())
    logging.info("Logging configured via observability module (fallback)")


def log_safe(value: object) -> str:
    """Strip CR/LF so a request-derived value (an id from the URL, an exception quoting it)
    cannot forge log lines."""
    return str(value).replace("\r", "").replace("\n", "")
