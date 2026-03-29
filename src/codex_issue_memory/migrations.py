from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from hashlib import sha256
from importlib import resources
import re
import sqlite3
from typing import Any

MIGRATION_NAME_RE = re.compile(r"^(?P<version>\d{3})_(?P<name>[a-z0-9_]+)\.sql$", re.IGNORECASE)


class MigrationError(RuntimeError):
    """Raised when schema migrations are inconsistent or invalid."""


@dataclass(frozen=True, slots=True)
class MigrationAsset:
    version: int
    name: str
    resource_name: str
    checksum: str
    sql: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SchemaState:
    current_version: int
    applied_count: int
    migrations: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MigrationRunner:
    """Discover and apply packaged SQL migrations."""

    def __init__(self, package: str = "codex_issue_memory") -> None:
        self.package = package

    def list_migrations(self) -> list[MigrationAsset]:
        sql_dir = resources.files(self.package).joinpath("sql")
        assets: list[MigrationAsset] = []
        for entry in sql_dir.iterdir():
            if not entry.is_file():
                continue
            match = MIGRATION_NAME_RE.match(entry.name)
            if not match:
                continue
            sql = entry.read_text(encoding="utf-8")
            assets.append(
                MigrationAsset(
                    version=int(match.group("version")),
                    name=match.group("name"),
                    resource_name=entry.name,
                    checksum=sha256(sql.encode("utf-8")).hexdigest(),
                    sql=sql,
                )
            )
        assets.sort(key=lambda item: item.version)
        if not assets:
            raise MigrationError("No SQL migrations were discovered")
        return assets

    @staticmethod
    def ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _has_schema_migrations_table(conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        return row is not None

    def applied_migrations(self, conn: sqlite3.Connection, *, create_if_missing: bool = True) -> list[dict[str, Any]]:
        if create_if_missing:
            self.ensure_schema_migrations_table(conn)
        elif not self._has_schema_migrations_table(conn):
            return []
        rows = conn.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def schema_state(self, conn: sqlite3.Connection) -> SchemaState:
        applied = self.applied_migrations(conn, create_if_missing=False)
        current_version = max((int(row["version"]) for row in applied), default=0)
        return SchemaState(
            current_version=current_version,
            applied_count=len(applied),
            migrations=applied,
        )

    def apply_all(self, conn: sqlite3.Connection, *, target_version: int | None = None) -> list[MigrationAsset]:
        self.ensure_schema_migrations_table(conn)
        migrations = self.list_migrations()
        applied_rows = self.applied_migrations(conn)
        applied_by_version = {int(row["version"]): row for row in applied_rows}

        executed: list[MigrationAsset] = []
        for migration in migrations:
            if target_version is not None and migration.version > target_version:
                break

            existing = applied_by_version.get(migration.version)
            if existing is not None:
                if str(existing["checksum"]) != migration.checksum:
                    raise MigrationError(
                        f"Migration checksum mismatch for version {migration.version}: "
                        f"database has {existing['checksum']}, package has {migration.checksum}"
                    )
                continue

            conn.executescript(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    self._utc_now_iso(),
                ),
            )
            executed.append(migration)

        return executed

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def inspect_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Convenience helper for callers that only need schema state."""
    return MigrationRunner().schema_state(conn).to_dict()
