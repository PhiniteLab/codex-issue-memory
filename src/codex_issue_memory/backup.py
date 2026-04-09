from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import shutil
import sqlite3
from typing import Any

from .migrations import inspect_schema
from .settings import Settings


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


@dataclass(slots=True)
class BackupResult:
    local_path: str
    mirror_path: str | None
    sha256: str
    created_at_utc: str
    bytes: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class BackupManager:
    """Create, inspect, and restore safe SQLite snapshots using the sqlite3 backup API."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @classmethod
    def from_env(cls) -> "BackupManager":
        return cls(Settings.from_env())

    def _manifest_path(self, sqlite_path: Path) -> Path:
        return sqlite_path.with_suffix(".json")

    def _hash_file(self, path: Path) -> str:
        hasher = sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)  # 1 MiB chunks
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    def _load_manifest(self, sqlite_path: Path) -> dict[str, Any] | None:
        manifest_path = self._manifest_path(sqlite_path)
        if not manifest_path.exists():
            return None
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_manifest(self, sqlite_path: Path, *, digest: str, created_at_utc: str) -> dict[str, Any]:
        manifest = {
            "created_at_utc": created_at_utc,
            "source_db": str(self.settings.db_path),
            "local_path": str(sqlite_path),
            "sha256": digest,
            "bytes": sqlite_path.stat().st_size,
            "hostname": self.settings.hostname,
        }
        with sqlite3.connect(sqlite_path) as manifest_conn:
            manifest_conn.row_factory = sqlite3.Row
            manifest["schema"] = inspect_schema(manifest_conn)
        manifest_path = self._manifest_path(sqlite_path)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def create_backup(self) -> BackupResult:
        self.settings.ensure_dirs()
        if not self.settings.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.settings.db_path}")

        stamp = utc_stamp()
        local_path = self.settings.backup_dir / f"issue_memory_{stamp}.sqlite3"

        src_conn = sqlite3.connect(self.settings.db_path, timeout=30.0)
        try:
            dst_conn = sqlite3.connect(local_path)
            try:
                src_conn.backup(dst_conn)
                dst_conn.commit()
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

        digest = self._hash_file(local_path)
        created_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self._write_manifest(local_path, digest=digest, created_at_utc=created_at_utc)

        mirror_path: Path | None = None
        if self.settings.windows_backup_target:
            self.settings.windows_backup_target.mkdir(parents=True, exist_ok=True)
            mirror_path = self.settings.windows_backup_target / local_path.name
            shutil.copy2(local_path, mirror_path)
            shutil.copy2(self._manifest_path(local_path), self._manifest_path(mirror_path))

        self._prune(self.settings.backup_dir, keep=self.settings.local_backup_keep)
        if self.settings.windows_backup_target:
            self._prune(self.settings.windows_backup_target, keep=self.settings.mirror_backup_keep)

        return BackupResult(
            local_path=str(local_path),
            mirror_path=str(mirror_path) if mirror_path else None,
            sha256=digest,
            created_at_utc=created_at_utc,
            bytes=local_path.stat().st_size,
        )

    def list_backups(self, *, limit: int = 20) -> list[dict[str, Any]]:
        self.settings.ensure_dirs()
        backups = sorted(
            self.settings.backup_dir.glob("issue_memory_*.sqlite3"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        rows: list[dict[str, Any]] = []
        for sqlite_path in backups[: max(limit, 1)]:
            manifest = self._load_manifest(sqlite_path) or {}
            actual_digest = self._hash_file(sqlite_path)
            manifest_digest = str(manifest.get("sha256", ""))
            rows.append(
                {
                    "path": str(sqlite_path),
                    "bytes": sqlite_path.stat().st_size,
                    "created_at_utc": str(manifest.get("created_at_utc", "")),
                    "sha256": actual_digest,
                    "manifest_present": bool(manifest),
                    "verified": bool(manifest) and manifest_digest == actual_digest,
                    "schema": manifest.get("schema"),
                }
            )
        return rows

    def verify_backup(self, backup_path: str | Path) -> dict[str, Any]:
        sqlite_path = Path(backup_path).expanduser()
        if sqlite_path.suffix.lower() == ".json":
            sqlite_path = sqlite_path.with_suffix(".sqlite3")
        if not sqlite_path.exists():
            raise FileNotFoundError(f"Backup not found: {sqlite_path}")
        manifest = self._load_manifest(sqlite_path)
        actual_digest = self._hash_file(sqlite_path)
        manifest_digest = str((manifest or {}).get("sha256", ""))
        return {
            "path": str(sqlite_path),
            "verified": bool(manifest) and manifest_digest == actual_digest,
            "manifest_present": bool(manifest),
            "actual_sha256": actual_digest,
            "manifest_sha256": manifest_digest or None,
            "schema": (manifest or {}).get("schema"),
        }

    def restore_backup(self, backup_path: str | Path, *, create_safety_backup: bool = True) -> dict[str, Any]:
        self.settings.ensure_dirs()
        sqlite_path = Path(backup_path).expanduser()
        if sqlite_path.suffix.lower() == ".json":
            sqlite_path = sqlite_path.with_suffix(".sqlite3")
        if not sqlite_path.exists():
            raise FileNotFoundError(f"Backup not found: {sqlite_path}")

        verification = self.verify_backup(sqlite_path)
        if not verification["verified"]:
            raise ValueError(f"Backup manifest verification failed for {sqlite_path}")

        safety_backup: BackupResult | None = None
        if create_safety_backup and self.settings.db_path.exists():
            safety_backup = self.create_backup()

        self.settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        src_conn = sqlite3.connect(sqlite_path, timeout=30.0)
        try:
            dst_conn = sqlite3.connect(self.settings.db_path, timeout=30.0)
            try:
                src_conn.backup(dst_conn)
                dst_conn.commit()
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

        with sqlite3.connect(self.settings.db_path) as conn:
            conn.row_factory = sqlite3.Row
            schema = inspect_schema(conn)

        return {
            "status": "ok",
            "restored_from": str(sqlite_path),
            "schema": schema,
            "safety_backup": safety_backup.to_dict() if safety_backup is not None else None,
        }

    @staticmethod
    def _prune(directory: Path, *, keep: int) -> None:
        sqlite_files = sorted(directory.glob("issue_memory_*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old_file in sqlite_files[keep:]:
            json_path = old_file.with_suffix(".json")
            try:
                old_file.unlink(missing_ok=True)
            except TypeError:
                if old_file.exists():
                    old_file.unlink()
            try:
                json_path.unlink(missing_ok=True)
            except TypeError:
                if json_path.exists():
                    json_path.unlink()
