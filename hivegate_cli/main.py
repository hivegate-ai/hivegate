import argparse
import os
import sys
from importlib import metadata, resources
from pathlib import Path

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
MODEL_KEYS = ("GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY", "ZAI_API_KEY", "DEEPSEEK_API_KEY")


def _read_template(name: str) -> str:
    return resources.files("hivegate_cli").joinpath("templates", name).read_text(encoding="utf-8")


def _render_env(text: str) -> str:
    # The server binds to 127.0.0.1 by default, so a first local run needs no API key.
    return text.replace("# AUTH_DISABLED=true", "AUTH_DISABLED=true")


def init_project(target: Path, force: bool = False) -> list[Path]:
    """Write the compose stack and a starter .env into target. Refuses to overwrite without force."""
    destinations = {target / relative: template for relative, template in TEMPLATE_FILES.items()}
    existing = [path for path in destinations if path.exists()]
    if existing and not force:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"Already exists: {names}. Use --force to overwrite.")

    for path, template in destinations.items():
        text = _read_template(template)
        if path.name == ".env":
            text = _render_env(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return list(destinations)


def _has_model_key() -> bool:
    return any(os.environ.get(key) not in (None, "", PLACEHOLDER_GOOGLE_KEY) for key in MODEL_KEYS)


def _cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.directory)
    try:
        init_project(target, force=args.force)
    except FileExistsError as e:
        print(e, file=sys.stderr)
        return 1

    print(f"Wrote compose.yaml, .env and the database setup files to {target.resolve()}\n")
    print("Next:")
    print("  1. Set a model key in .env (the demo agents use GOOGLE_API_KEY)")
    print("  2. docker compose up -d")
    print("  3. hivegate serve")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    # load_dotenv() in api.main searches upward from its own file, which for an installed
    # package is site-packages, so the .env next to the user has to be loaded here first.
    load_dotenv(Path.cwd() / ".env")
    if not _has_model_key():
        print("Warning: no model key is set, so agents cannot answer. Set one in .env.", file=sys.stderr)

    import uvicorn

    uvicorn.run("api.main:app", host=args.host, port=args.port, reload=args.reload)
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
    serve.add_argument("--host", default="127.0.0.1", help="Default 127.0.0.1. Use 0.0.0.0 only with auth enabled.")
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    serve.add_argument("--reload", action="store_true", help="Restart on code changes")
    serve.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
