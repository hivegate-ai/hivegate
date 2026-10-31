import argparse
import os
import secrets
import sys
from importlib import metadata, resources
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

# Packaged copies of the repo's compose stack, written to the same relative paths so
# compose.yaml's volume mounts resolve. tests/test_hivegate_cli.py fails if they drift.
TEMPLATE_FILES = {
    "compose.yaml": "compose.yaml",
    ".env": "env.example",
    "db/migrations/setup.sql": "db/migrations/setup.sql",
    "sql/sample_data.sql": "sql/sample_data.sql",
}

PLACEHOLDER_GOOGLE_KEY = "your-google-api-key"
PLACEHOLDER_ADMIN_SECRET = "your-admin-secret"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
MODEL_KEYS = ("GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY", "ZAI_API_KEY", "DEEPSEEK_API_KEY")


def _read_template(name: str) -> str:
    return resources.files("hivegate_cli").joinpath("templates", name).read_text(encoding="utf-8")


def _render_env(text: str) -> str:
    # The server binds to 127.0.0.1 by default, so a first local run needs no API key.
    # `serve` refuses any other host while this is on.
    text = text.replace("# AUTH_DISABLED=true", "AUTH_DISABLED=true")
    text = text.replace(f"ADMIN_SECRET={PLACEHOLDER_ADMIN_SECRET}", f"ADMIN_SECRET={secrets.token_urlsafe(32)}")
    return text.replace("\nSECRET_TOKEN_ENC_KEY=\n", f"\nSECRET_TOKEN_ENC_KEY={Fernet.generate_key().decode()}\n")


def init_project(target: Path, force: bool = False) -> list[Path]:
    """Write the compose stack and a starter .env into target. Returns the files written.

    Refuses to overwrite without force. Even with force an existing .env is kept: it holds
    the user's API keys and the key their stored tokens are encrypted with.
    """
    destinations = {target / relative: template for relative, template in TEMPLATE_FILES.items()}
    existing = [path for path in destinations if path.exists()]
    if existing and not force:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"Already exists: {names}. Use --force to overwrite (an existing .env is kept).")

    written = []
    for path, template in destinations.items():
        if path.name == ".env" and path.exists():
            continue
        text = _read_template(template)
        if path.name == ".env":
            text = _render_env(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def _has_model_key() -> bool:
    return any(os.environ.get(key) not in (None, "", PLACEHOLDER_GOOGLE_KEY) for key in MODEL_KEYS)


def _cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.directory)
    try:
        written = init_project(target, force=args.force)
    except FileExistsError as e:
        print(e, file=sys.stderr)
        return 1

    names = ", ".join(str(path.relative_to(target)) for path in written)
    print(f"Wrote {names} to {target.resolve()}\n")
    print("Next:")
    print("  1. Set a model key in .env (the demo agents use GOOGLE_API_KEY)")
    print("  2. docker compose up -d")
    print("  3. hivegate serve")
    return 0


def _load_env() -> None:
    # load_dotenv() in api.main and db.url searches upward from its own file, which for an
    # installed package is site-packages, so the .env next to the user has to be loaded first.
    load_dotenv(Path.cwd() / ".env")


def _exposure_problem(host: str) -> str | None:
    """Why this configuration must not listen beyond this machine, if it must not."""
    if host in LOCAL_HOSTS:
        return None

    from api.services.auth import is_auth_disabled

    if is_auth_disabled():
        return f"Refusing to serve on {host} with AUTH_DISABLED on: every route would be open. Remove it from .env."
    if os.environ.get("ADMIN_SECRET") == PLACEHOLDER_ADMIN_SECRET:
        return f"Refusing to serve on {host} with the example ADMIN_SECRET. Set your own in .env."
    return None


def _cmd_serve(args: argparse.Namespace) -> int:
    _load_env()
    try:
        port = args.port if args.port is not None else int(os.environ.get("PORT", "8000"))
    except ValueError:
        print(f"PORT must be a number, got {os.environ['PORT']!r}", file=sys.stderr)
        return 1

    problem = _exposure_problem(args.host)
    if problem:
        print(problem, file=sys.stderr)
        return 1

    if not _has_model_key():
        print("Warning: no model key is set, so agents cannot answer. Set one in .env.", file=sys.stderr)

    import uvicorn

    uvicorn.run("api.main:app", host=args.host, port=port, reload=args.reload)
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    _load_env()

    from sqlalchemy import create_engine
    from sqlalchemy.exc import SQLAlchemyError

    from db.url import get_db_url
    from hivegate_cli.migrate import MigrationError, apply_migrations, packaged_migrations

    try:
        applied = apply_migrations(create_engine(get_db_url()), packaged_migrations())
    except (MigrationError, SQLAlchemyError) as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        return 1

    print(f"Applied: {', '.join(applied)}" if applied else "Database is up to date.")
    return 0


def _version() -> str:
    try:
        return metadata.version("hivegate")
    except metadata.PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hivegate", description="Run HiveGate, a production API for Agno agents.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Write compose.yaml and a starter .env for local Postgres and Qdrant")
    init.add_argument("directory", nargs="?", default=".", help="Where to write the files (default: here)")
    init.add_argument("--force", action="store_true", help="Overwrite files that already exist")
    init.set_defaults(func=_cmd_init)

    serve = commands.add_parser("serve", help="Start the API server, reading .env from the current directory")
    serve.add_argument("--host", default="127.0.0.1", help="Default 127.0.0.1. Any other host requires auth enabled.")
    serve.add_argument("--port", type=int, help="Default: PORT from the environment or .env, else 8000")
    serve.add_argument("--reload", action="store_true", help="Restart on code changes")
    serve.set_defaults(func=_cmd_serve)

    migrate = commands.add_parser("migrate", help="Apply database changes after an upgrade, reading .env from here")
    migrate.set_defaults(func=_cmd_migrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
