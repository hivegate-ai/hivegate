import re
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

from hivegate_cli.main import TEMPLATE_FILES, _has_model_key, _read_template, build_parser, init_project, main
from hivegate_cli.migrate import MigrationError, apply_migrations, packaged_migrations

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


def test_init_force_refreshes_files_but_keeps_env(tmp_path):
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=my-own-key\n")
    (tmp_path / "compose.yaml").write_text("old")

    written = init_project(tmp_path, force=True)

    assert (tmp_path / ".env").read_text() == "GOOGLE_API_KEY=my-own-key\n"
    assert (tmp_path / "compose.yaml").read_text() == _read_template("compose.yaml")
    assert tmp_path / ".env" not in written


def _env_value(path, key):
    lines = (path / ".env").read_text().splitlines()
    return next(line.split("=", 1)[1] for line in lines if line.startswith(f"{key}="))


def test_init_generates_secrets_unique_to_each_project(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    init_project(first)
    init_project(second)

    admin_secret = _env_value(first, "ADMIN_SECRET")
    assert admin_secret != "your-admin-secret"
    assert admin_secret != _env_value(second, "ADMIN_SECRET")
    # Fernet rejects anything that is not a real key
    Fernet(_env_value(first, "SECRET_TOKEN_ENC_KEY"))


def test_compose_and_app_read_the_same_database_settings():
    compose = _read_template("compose.yaml")
    assert "${DB_PASS:-" in compose
    assert "${DB_DATABASE:-" in compose
    env = _read_template("env.example").splitlines()
    assert any(line.startswith("DB_PASS=") for line in env)
    assert any(line.startswith("DB_DATABASE=") for line in env)


def test_compose_publishes_ports_on_localhost_only():
    ports = [line.strip() for line in _read_template("compose.yaml").splitlines() if line.strip().startswith('- "')]
    published = [line for line in ports if line.count(":") >= 1 and "/" not in line]
    assert published
    assert all(line.startswith('- "127.0.0.1:') for line in published)


def test_serve_binds_to_localhost_by_default():
    assert build_parser().parse_args(["serve"]).host == "127.0.0.1"


@pytest.fixture
def serve_env(tmp_path, monkeypatch):
    """A clean working directory and environment, with uvicorn replaced by a recorder."""
    monkeypatch.chdir(tmp_path)
    for key in ("AUTH_DISABLED", "ADMIN_SECRET", "PORT"):
        monkeypatch.delenv(key, raising=False)
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(kwargs))
    return calls


def test_serve_refuses_a_public_host_with_auth_disabled(serve_env, monkeypatch, capsys):
    monkeypatch.setenv("AUTH_DISABLED", "true")

    assert main(["serve", "--host", "0.0.0.0"]) == 1

    assert serve_env == []
    assert "AUTH_DISABLED" in capsys.readouterr().err


def test_serve_refuses_a_public_host_with_the_example_admin_secret(serve_env, monkeypatch, capsys):
    monkeypatch.setenv("ADMIN_SECRET", "your-admin-secret")

    assert main(["serve", "--host", "0.0.0.0"]) == 1

    assert serve_env == []
    assert "ADMIN_SECRET" in capsys.readouterr().err


def test_serve_allows_a_public_host_with_auth_on(serve_env, monkeypatch):
    monkeypatch.setenv("ADMIN_SECRET", "something-private")

    assert main(["serve", "--host", "0.0.0.0"]) == 0

    assert serve_env[0]["host"] == "0.0.0.0"


def test_serve_allows_auth_disabled_on_localhost(serve_env, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")

    assert main(["serve"]) == 0

    assert serve_env[0]["host"] == "127.0.0.1"


def test_serve_reads_port_from_dotenv(serve_env, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("PORT=9000\n")
    # load_dotenv sets os.environ directly; registering PORT here makes monkeypatch remove it afterwards
    monkeypatch.setenv("PORT", "1")
    monkeypatch.delenv("PORT")

    assert main(["serve"]) == 0

    assert serve_env[0]["port"] == 9000


def test_port_flag_beats_the_environment(serve_env, monkeypatch):
    monkeypatch.setenv("PORT", "9000")

    assert main(["serve", "--port", "7000"]) == 0

    assert serve_env[0]["port"] == 7000


def test_bad_port_only_fails_serve(serve_env, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PORT", "8080x")

    assert main(["init", str(tmp_path / "project")]) == 0
    assert main(["serve"]) == 1

    assert serve_env == []
    assert "8080x" in capsys.readouterr().err


def _engine(tmp_path):
    return create_engine(f"sqlite:///{tmp_path / 'migrate.db'}")


def test_migrations_apply_once(tmp_path):
    engine = _engine(tmp_path)
    migrations = [("001.sql", "CREATE TABLE one (id INTEGER)")]

    assert apply_migrations(engine, migrations) == ["001.sql"]
    assert apply_migrations(engine, migrations) == []


def test_only_new_migrations_apply(tmp_path):
    engine = _engine(tmp_path)
    apply_migrations(engine, [("001.sql", "CREATE TABLE one (id INTEGER)")])

    applied = apply_migrations(
        engine, [("001.sql", "CREATE TABLE one (id INTEGER)"), ("002.sql", "CREATE TABLE two (id INTEGER)")]
    )

    assert applied == ["002.sql"]


def test_failed_migration_is_not_recorded_and_stops_the_rest(tmp_path):
    engine = _engine(tmp_path)

    with pytest.raises(MigrationError, match="001.sql"):
        apply_migrations(engine, [("001.sql", "NOT SQL"), ("002.sql", "CREATE TABLE two (id INTEGER)")])

    with engine.connect() as conn:
        assert conn.execute(text("SELECT name FROM hivegate_migrations")).all() == []


def test_packaged_migrations_quote_the_reserved_references_column():
    # Postgres rejects it unquoted, which only a run against a real server would show
    for name, sql in packaged_migrations():
        assert not re.search(r"^\s*references\b", sql, re.MULTILINE | re.IGNORECASE), name


def test_setup_sql_is_packaged_first():
    names = [name for name, _ in packaged_migrations()]

    assert names[0] == "setup.sql"
    assert names[1:] == sorted(names[1:])
    assert "011_token_usage_cost.sql" in names


@pytest.mark.parametrize(
    "value,expected",
    [("your-google-api-key", False), ("", False), ("a-real-key", True)],
)
def test_placeholder_key_does_not_count_as_a_model_key(monkeypatch, value, expected):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY", "ZAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", value)

    assert _has_model_key() is expected
