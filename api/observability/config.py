import os
from enum import Enum
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_core.core_schema import FieldValidationInfo
from pydantic_settings import BaseSettings


class TracingBackend(str, Enum):
    """Supported tracing backends."""

    NONE = "none"
    CONSOLE = "console"
    OTLP = "otlp"
    SENTRY = "sentry"


class LoggingBackend(str, Enum):
    """Supported logging backends."""

    CONSOLE = "console"
    OTLP = "otlp"
    LOGTAIL = "logtail"


class ObservabilitySettings(BaseSettings):
    """Observability configuration using environment variables.

    Supports multiple backends for tracing and logging with OpenTelemetry
    as the standard protocol.

    Environment variables use OTEL_ prefix for new settings, while legacy
    SENTRY_DSN and BETTERSTACK_* variables are still supported for backward
    compatibility.
    """

    # Service identification
    service_name: str = Field(default="hivegate")
    service_version: str = Field(default="1.0.0")
    environment: str = Field(default="development")

    # Tracing configuration
    tracing_enabled: bool = Field(default=True)
    tracing_backend: TracingBackend = Field(default=TracingBackend.CONSOLE)
    tracing_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)

    # Logging configuration (supports multiple backends)
    logging_backends: List[LoggingBackend] = Field(default=[LoggingBackend.CONSOLE])
    log_level: str = Field(default="INFO")

    # OTLP endpoint configuration (for OTLP backends)
    otlp_endpoint: Optional[str] = Field(default=None)
    otlp_headers: Optional[str] = Field(default=None)
    otlp_insecure: bool = Field(default=False)

    # Legacy Sentry configuration (backward compatibility)
    sentry_dsn: Optional[str] = Field(default=None)
    sentry_traces_sample_rate: float = Field(default=1.0)
    sentry_profiles_sample_rate: float = Field(default=1.0)

    # Legacy Better Stack/Logtail configuration (backward compatibility)
    betterstack_source_token: Optional[str] = Field(default=None)
    betterstack_host: Optional[str] = Field(default=None)

    @field_validator("service_name", mode="before")
    @classmethod
    def set_service_name(cls, v: str, info: FieldValidationInfo) -> str:
        return os.environ.get("OTEL_SERVICE_NAME", v or "hivegate")

    @field_validator("service_version", mode="before")
    @classmethod
    def set_service_version(cls, v: str, info: FieldValidationInfo) -> str:
        return os.environ.get("OTEL_SERVICE_VERSION", v or "1.0.0")

    @field_validator("environment", mode="before")
    @classmethod
    def set_environment(cls, v: str, info: FieldValidationInfo) -> str:
        return os.environ.get("OTEL_ENVIRONMENT", os.environ.get("ENVIRONMENT", v or "development"))

    @field_validator("tracing_enabled", mode="before")
    @classmethod
    def set_tracing_enabled(cls, v: bool, info: FieldValidationInfo) -> bool:
        env_val = os.environ.get("OTEL_TRACING_ENABLED", "").lower()
        if env_val in ("true", "1", "yes"):
            return True
        if env_val in ("false", "0", "no"):
            return False
        return v if v is not None else True

    @field_validator("tracing_backend", mode="before")
    @classmethod
    def set_tracing_backend(cls, v: str, info: FieldValidationInfo) -> TracingBackend:
        backend_str = os.environ.get("OTEL_TRACING_BACKEND", v or "console").lower()
        try:
            return TracingBackend(backend_str)
        except ValueError:
            return TracingBackend.CONSOLE

    @field_validator("tracing_sample_rate", mode="before")
    @classmethod
    def set_tracing_sample_rate(cls, v: float, info: FieldValidationInfo) -> float:
        env_val = os.environ.get("OTEL_TRACING_SAMPLE_RATE")
        if env_val:
            try:
                return max(0.0, min(1.0, float(env_val)))
            except ValueError:
                pass
        return v if v is not None else 1.0

    @field_validator("logging_backends", mode="before")
    @classmethod
    def set_logging_backends(cls, v: List[LoggingBackend], info: FieldValidationInfo) -> List[LoggingBackend]:
        env_val = os.environ.get("OTEL_LOGGING_BACKEND", "")
        if env_val:
            backends = []
            for backend_str in env_val.lower().split(","):
                backend_str = backend_str.strip()
                try:
                    backends.append(LoggingBackend(backend_str))
                except ValueError:
                    pass
            if backends:
                return backends
        return v if v else [LoggingBackend.CONSOLE]

    @field_validator("log_level", mode="before")
    @classmethod
    def set_log_level(cls, v: str, info: FieldValidationInfo) -> str:
        return os.environ.get("OTEL_LOG_LEVEL", v or "INFO").upper()

    @field_validator("otlp_endpoint", mode="before")
    @classmethod
    def set_otlp_endpoint(cls, v: Optional[str], info: FieldValidationInfo) -> Optional[str]:
        return os.environ.get("OTEL_OTLP_ENDPOINT", v)

    @field_validator("otlp_headers", mode="before")
    @classmethod
    def set_otlp_headers(cls, v: Optional[str], info: FieldValidationInfo) -> Optional[str]:
        return os.environ.get("OTEL_OTLP_HEADERS", v)

    @field_validator("otlp_insecure", mode="before")
    @classmethod
    def set_otlp_insecure(cls, v: bool, info: FieldValidationInfo) -> bool:
        env_val = os.environ.get("OTEL_OTLP_INSECURE", "").lower()
        if env_val in ("true", "1", "yes"):
            return True
        if env_val in ("false", "0", "no"):
            return False
        return v if v is not None else False

    @field_validator("sentry_dsn", mode="before")
    @classmethod
    def set_sentry_dsn(cls, v: Optional[str], info: FieldValidationInfo) -> Optional[str]:
        return os.environ.get("SENTRY_DSN", v)

    @field_validator("sentry_traces_sample_rate", mode="before")
    @classmethod
    def set_sentry_traces_sample_rate(cls, v: float, info: FieldValidationInfo) -> float:
        env_val = os.environ.get("SENTRY_TRACES_SAMPLE_RATE")
        if env_val:
            try:
                return max(0.0, min(1.0, float(env_val)))
            except ValueError:
                pass
        return v if v is not None else 1.0

    @field_validator("sentry_profiles_sample_rate", mode="before")
    @classmethod
    def set_sentry_profiles_sample_rate(cls, v: float, info: FieldValidationInfo) -> float:
        env_val = os.environ.get("SENTRY_PROFILES_SAMPLE_RATE")
        if env_val:
            try:
                return max(0.0, min(1.0, float(env_val)))
            except ValueError:
                pass
        return v if v is not None else 1.0

    @field_validator("betterstack_source_token", mode="before")
    @classmethod
    def set_betterstack_source_token(cls, v: Optional[str], info: FieldValidationInfo) -> Optional[str]:
        return os.environ.get("BETTERSTACK_SOURCE_TOKEN", v)

    @field_validator("betterstack_host", mode="before")
    @classmethod
    def set_betterstack_host(cls, v: Optional[str], info: FieldValidationInfo) -> Optional[str]:
        return os.environ.get("BETTERSTACK_HOST", v)


# Singleton instance
observability_settings = ObservabilitySettings()
