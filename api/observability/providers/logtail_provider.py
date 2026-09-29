import json
import logging
from typing import TYPE_CHECKING, Optional

from api.observability.providers.base import LoggingProvider

if TYPE_CHECKING:
    from api.observability.config import ObservabilitySettings

logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging.

    Formats log records as JSON objects with level, message, logger name,
    and timestamp. This format is optimal for log aggregation services
    like Better Stack/Logtail.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "timestamp": self.formatTime(record),
        }

        # Include trace context if available
        if hasattr(record, "otelTraceID") and record.otelTraceID:
            log_data["trace_id"] = record.otelTraceID
        if hasattr(record, "otelSpanID") and record.otelSpanID:
            log_data["span_id"] = record.otelSpanID

        # Include exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class LogtailLoggingProvider(LoggingProvider):
    """Better Stack/Logtail logging provider.

    Sends JSON-formatted logs to Better Stack (formerly Logtail) for
    centralized log aggregation. Requires BETTERSTACK_SOURCE_TOKEN
    environment variable or configuration setting.
    """

    def __init__(self) -> None:
        self._config: Optional["ObservabilitySettings"] = None
        self._handler: Optional[logging.Handler] = None

    def initialize(self, config: "ObservabilitySettings") -> None:
        """Initialize with configuration.

        Args:
            config: ObservabilitySettings with Better Stack settings.
        """
        self._config = config

        if not config.betterstack_source_token:
            logger.warning("Better Stack source token not configured")

    def shutdown(self) -> None:
        """Flush pending logs to Better Stack."""
        if self._handler:
            try:
                self._handler.flush()
            except Exception as e:
                logger.debug(f"Error flushing Logtail handler: {e}")

    def get_handler(self) -> logging.Handler:
        """Return a Logtail logging handler.

        Returns:
            A configured LogtailHandler instance.

        Raises:
            ValueError: If Better Stack source token is not configured.
        """
        if self._handler is not None:
            return self._handler

        if not self._config or not self._config.betterstack_source_token:
            raise ValueError("BETTERSTACK_SOURCE_TOKEN is required for Logtail logging")

        try:
            from logtail import LogtailHandler

            source_token = self._config.betterstack_source_token
            host = self._config.betterstack_host
            # Omit host when unset so logtail falls back to its own default endpoint.
            self._handler = (
                LogtailHandler(source_token=source_token, host=host)
                if host
                else LogtailHandler(source_token=source_token)
            )
            self._handler.setFormatter(JsonFormatter())

            host_info = self._config.betterstack_host or "default"
            logger.info(f"Logtail logging handler configured (host={host_info})")

            return self._handler
        except ImportError:
            raise ImportError("logtail-python package is required for Logtail logging provider")
        except Exception as e:
            raise RuntimeError(f"Failed to create Logtail handler: {e}")
