from pathlib import Path


def test_env_example_documents_every_api_settings_environment_variable() -> None:
    """Keep the copy-paste configuration in sync with api/settings.py."""
    env_text = Path(__file__).parents[1].joinpath(".env.example").read_text()
    documented = {
        line.split("=", 1)[0]
        for line in env_text.splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    # These are the names read by ApiSettings validators (not the Python field
    # names: e.g. GOOGLE_API_KEY feeds gemini_api_key and TESTING feeds debug).
    settings_env = {
        "QDRANT_URL",
        "QDRANT_API_KEY",
        "QDRANT_PORT",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "XAI_API_KEY",
        "ZAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "BRIGHT_DATA_API_KEY",
        "BRIGHT_DATA_WEB_UNLOCKER_ZONE",
        "BRIGHT_DATA_SERP_ZONE",
        "TESTING",
        "ENABLE_GUARDRAILS",
    }
    assert settings_env <= documented
