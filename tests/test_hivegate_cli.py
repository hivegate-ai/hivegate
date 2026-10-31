from pathlib import Path

import pytest

from hivegate_cli.main import TEMPLATE_FILES, _has_model_key, _read_template, build_parser, init_project

REPO_FILES = {
    "compose.yaml": "compose.yaml",
    "env.example": ".env.example",
    "db/migrations/setup.sql": "db/migrations/setup.sql",
    "sql/sample_data.sql": "sql/sample_data.sql",
}


@pytest.mark.parametrize("template,repo_file", REPO_FILES.items())
def test_templates_match_the_repo_files(template, repo_file):
    assert _read_template(template) == Path(repo_file).read_text(encoding="utf-8")


def test_init_writes_every_file_compose_mounts(tmp_path):
    init_project(tmp_path)

    for relative in TEMPLATE_FILES:
        assert (tmp_path / relative).is_file()
    compose = (tmp_path / "compose.yaml").read_text()
    assert "./db/migrations/setup.sql:" in compose
    assert "./sql/sample_data.sql:" in compose


def test_init_enables_local_auth_bypass_only_in_the_written_env(tmp_path):
    init_project(tmp_path)

    env_lines = (tmp_path / ".env").read_text().splitlines()
    assert "AUTH_DISABLED=true" in env_lines
    assert "# AUTH_DISABLED=true" in _read_template("env.example").splitlines()


def test_init_refuses_to_overwrite(tmp_path):
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=my-own-key\n")

    with pytest.raises(FileExistsError):
        init_project(tmp_path)

    assert (tmp_path / ".env").read_text() == "GOOGLE_API_KEY=my-own-key\n"
    assert not (tmp_path / "compose.yaml").exists()


def test_init_force_overwrites(tmp_path):
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=my-own-key\n")

    init_project(tmp_path, force=True)

    assert "my-own-key" not in (tmp_path / ".env").read_text()


def test_serve_binds_to_localhost_by_default():
    assert build_parser().parse_args(["serve"]).host == "127.0.0.1"


@pytest.mark.parametrize(
    "value,expected",
    [("your-google-api-key", False), ("", False), ("a-real-key", True)],
)
def test_placeholder_key_does_not_count_as_a_model_key(monkeypatch, value, expected):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY", "ZAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", value)

    assert _has_model_key() is expected
