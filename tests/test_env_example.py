from pathlib import Path

from api.settings import ApiSettings


def test_env_example_covers_api_settings():
    env_example = Path(".env.example")
    env_text = env_example.read_text()

    # Collect variables documented in .env.example.
    # Commented-out variables still count as documented.
    env_keys = set()

    for line in env_text.splitlines():
        line = line.strip()

        if line.startswith("#"):
            line = line[1:].strip()

        if not line or "=" not in line:
            continue

        key = line.split("=", 1)[0].strip()
        env_keys.add(key)

    # ApiSettings fields that intentionally map to a different
    # environment-variable name.
    field_to_env = {
        "gemini_api_key": "GOOGLE_API_KEY",
        "agent_debug_mode": "TESTING", # see #82
    }

    # These fields are handled internally and do not directly read
    # an environment variable.
    ignored_fields = {
        "title",
        "version",
    }

    missing = []

    for field_name in ApiSettings.model_fields:
        if field_name in ignored_fields:
            continue

        expected_env_key = field_to_env.get(
            field_name,
            field_name.upper(),
        )

        if expected_env_key not in env_keys:
            missing.append(expected_env_key)

    assert not missing, "ApiSettings environment variables missing from .env.example: " + ", ".join(sorted(missing))
