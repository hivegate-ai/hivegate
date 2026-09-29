import os
from typing import List

from pydantic import Field, field_validator
from pydantic_core.core_schema import FieldValidationInfo
from pydantic_settings import BaseSettings

from api.services import str_to_bool


class ApiSettings(BaseSettings):
    """Api settings that are set using environment variables."""

    title: str = "agents-gateway"
    version: str = "1.0"

    # Set to False to disable docs at /docs and /redoc
    docs_enabled: bool = True

    # Cors origin list to allow requests from.
    # This list is set using the set_cors_origin_list validator
    # which uses the runtime_env variable to set the
    # default cors origin list.
    cors_origin_list: List[str] = Field(default_factory=list)

    # Qdrant vector database URL (optional)
    qdrant_url: str = Field(default="", description="Qdrant server URL for dynamic knowledge base")

    # Qdrant API key (optional)
    qdrant_api_key: str = Field(default="", description="Qdrant API key for authentication")

    # Qdrant port (optional)
    qdrant_port: int = Field(default=6333, description="Qdrant server port")

    gemini_api_key: str = Field(default="", description="Gemini API key")
    openai_api_key: str = Field(default="", description="OpenAI API key")
    anthropic_api_key: str = Field(default="", description="Anthropic API key")
    xai_api_key: str = Field(default="", description="xAI API key (Grok models)")
    zai_api_key: str = Field(default="", description="Z.ai API key (GLM models)")
    deepseek_api_key: str = Field(default="", description="DeepSeek API key")

    # Bright Data settings
    bright_data_api_key: str = Field(default="", description="Bright Data API key")
    bright_data_web_unlocker_zone: str = Field(default="web_unlocker1", description="Bright Data Web Unlocker zone")
    bright_data_serp_zone: str = Field(default="serp_api1", description="Bright Data SERP zone")

    # Agent debug mode - enabled only when TESTING=true
    agent_debug_mode: bool = Field(default=False, description="Enable debug mode for agents")

    # Guardrails - prompt injection and PII detection
    enable_guardrails: bool = Field(
        default=False, description="Enable input guardrails (prompt injection + PII detection)"
    )

    @field_validator("cors_origin_list", mode="before")
    def set_cors_origin_list(cls, cors_origin_list, info: FieldValidationInfo):
        # Defensive copy in case it's already a list
        valid_cors = list(cors_origin_list) if cors_origin_list else []

        valid_cors.extend(["http://localhost", "http://localhost:3000"])

        return list(set(valid_cors))  # Avoid duplicates if needed

    @field_validator("qdrant_url", mode="before")
    def set_qdrant_url(cls, qdrant_url, info: FieldValidationInfo):
        return os.environ.get("QDRANT_URL", "")

    @field_validator("qdrant_api_key", mode="before")
    def set_qdrant_api_key(cls, qdrant_api_key, info: FieldValidationInfo):
        return os.environ.get("QDRANT_API_KEY", "")

    @field_validator("qdrant_port", mode="before")
    def set_qdrant_port(cls, qdrant_port, info: FieldValidationInfo):
        port_str = os.environ.get("QDRANT_PORT", "6333")
        try:
            return int(port_str)
        except ValueError:
            return 6333

    @field_validator("gemini_api_key", mode="before")
    def set_gemini_api_key(cls, gemini_api_key, info: FieldValidationInfo):
        return os.environ.get("GOOGLE_API_KEY", "")

    @field_validator("openai_api_key", mode="before")
    def set_openai_api_key(cls, openai_api_key, info: FieldValidationInfo):
        return os.environ.get("OPENAI_API_KEY", "")

    @field_validator("anthropic_api_key", mode="before")
    def set_anthropic_api_key(cls, anthropic_api_key, info: FieldValidationInfo):
        return os.environ.get("ANTHROPIC_API_KEY", "")

    @field_validator("xai_api_key", mode="before")
    def set_xai_api_key(cls, xai_api_key, info: FieldValidationInfo):
        return os.environ.get("XAI_API_KEY", "")

    @field_validator("zai_api_key", mode="before")
    def set_zai_api_key(cls, zai_api_key, info: FieldValidationInfo):
        return os.environ.get("ZAI_API_KEY", "")

    @field_validator("deepseek_api_key", mode="before")
    def set_deepseek_api_key(cls, deepseek_api_key, info: FieldValidationInfo):
        return os.environ.get("DEEPSEEK_API_KEY", "")

    @field_validator("bright_data_api_key", mode="before")
    def set_bright_data_api_key(cls, bright_data_api_key, info: FieldValidationInfo):
        return os.environ.get("BRIGHT_DATA_API_KEY", "")

    @field_validator("bright_data_web_unlocker_zone", mode="before")
    def set_bright_data_web_unlocker_zone(cls, bright_data_web_unlocker_zone, info: FieldValidationInfo):
        return os.environ.get("BRIGHT_DATA_WEB_UNLOCKER_ZONE", "web_unlocker1")

    @field_validator("bright_data_serp_zone", mode="before")
    def set_bright_data_serp_zone(cls, bright_data_serp_zone, info: FieldValidationInfo):
        return os.environ.get("BRIGHT_DATA_SERP_ZONE", "serp_api1")

    @field_validator("agent_debug_mode", mode="before")
    def set_agent_debug_mode(cls, agent_debug_mode, info: FieldValidationInfo):
        # Enable debug mode only when TESTING=true
        return str_to_bool(os.environ.get("TESTING", "false"))

    @field_validator("enable_guardrails", mode="before")
    def set_enable_guardrails(cls, enable_guardrails, info: FieldValidationInfo):
        return str_to_bool(os.environ.get("ENABLE_GUARDRAILS", "false"))


# Create ApiSettings object
api_settings = ApiSettings()
