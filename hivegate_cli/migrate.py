from collections.abc import Iterable
from importlib import resources

from sqlalchemy import text
from sqlalchemy.engine import Engine

TRACKING_TABLE = "hivegate_migrations"


class MigrationError(Exception):
    """A migration file failed. Its transaction was rolled back and it was not recorded."""


def packaged_migrations() -> list[tuple[str, str]]:
    """The SQL files shipped in the package: setup.sql first, then the numbered files in order."""
    directory = resources.files("db").joinpath("migrations")
    names = sorted(entry.name for entry in directory.iterdir() if entry.name.endswith(".sql"))
    names.sort(key=lambda name: name != "setup.sql")
    return [(name, directory.joinpath(name).read_text(encoding="utf-8")) for name in names]


def apply_migrations(engine: Engine, migrations: Iterable[tuple[str, str]]) -> list[str]:
    """Apply what has not been recorded, each file in its own transaction. Returns the names applied.

    The first run on an existing database re-applies everything once. That is safe because
    these files are written to be re-runnable, which is also how setup.sql already ships.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {TRACKING_TABLE} "
                "(name VARCHAR(255) PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        done = {row[0] for row in conn.execute(text(f"SELECT name FROM {TRACKING_TABLE}"))}

    applied = []
    for name, sql in migrations:
        if name in done:
            continue
        try:
            with engine.begin() as conn:
                # The DBAPI cursor, not text(): these files hold several statements, and text()
                # would read a colon followed by a word as a bind parameter. It raises the
                # driver's own errors rather than SQLAlchemy's, hence the broad except.
                conn.connection.cursor().execute(sql)
                conn.execute(text(f"INSERT INTO {TRACKING_TABLE} (name) VALUES (:name)"), {"name": name})
        except Exception as e:
            raise MigrationError(f"{name}: {e}") from e
        applied.append(name)
    return applied
