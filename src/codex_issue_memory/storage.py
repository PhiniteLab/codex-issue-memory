from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from .lifecycle import read_server_lifecycle_status
from .migrations import MigrationRunner, SchemaState
from .normalization import comma_join, parse_tag_string, tokenize
from .settings import Settings

_logger = logging.getLogger(__name__)


STRATEGY_PRIOR_ALPHA = 2.0
STRATEGY_PRIOR_BETA = 2.0
VARIANT_PRIOR_ALPHA = 1.0
VARIANT_PRIOR_BETA = 1.0
STRONG_GLOBAL_FEEDBACK = {'fix_verified', 'false_positive'}

WEAK_FEEDBACK_WEIGHTS: dict[str, float] = {
    'candidate_accepted': 0.35,
    'candidate_rejected': 0.25,
    'merge_confirmed': 0.40,
    'merge_rejected': 0.40,
    'split_confirmed': 0.40,
    'split_rejected': 0.40,
}
WEAK_POSITIVE_FEEDBACK = {'candidate_accepted', 'merge_confirmed', 'split_confirmed'}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(slots=True)
class PatternBundle:
    pattern: dict[str, Any]
    examples: list[dict[str, Any]] = field(default_factory=list)
    variants: list[dict[str, Any]] = field(default_factory=list)
    episodes: list[dict[str, Any]] = field(default_factory=list)


class IssueMemoryStore:
    """SQLite-backed persistence layer for issue memory."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._migration_runner = MigrationRunner()

    @classmethod
    def from_env(cls) -> "IssueMemoryStore":
        return cls(Settings.from_env())

    def connect(self) -> sqlite3.Connection:
        self.settings.ensure_dirs()
        conn = sqlite3.connect(self.settings.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        return conn

    @contextmanager
    def managed_connection(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            if immediate:
                conn.execute('BEGIN IMMEDIATE;')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.managed_connection() as conn:
            self._migration_runner.apply_all(conn)

    def migrate(self, *, target_version: int | None = None) -> SchemaState:
        with self.managed_connection() as conn:
            self._migration_runner.apply_all(conn, target_version=target_version)
            return self._migration_runner.schema_state(conn)

    def schema_state(self) -> SchemaState:
        with self.managed_connection() as conn:
            return self._migration_runner.schema_state(conn)

    def report_dir(self) -> Path:
        report_dir = self.settings.state_dir / 'reports'
        report_dir.mkdir(parents=True, exist_ok=True)
        return report_dir

    def save_report(self, name: str, payload: dict[str, Any]) -> Path:
        report_path = self.report_dir() / f"{name}.json"
        report_path.write_text(self._json_dumps(payload), encoding='utf-8')
        return report_path

    def list_saved_reports(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.report_dir().glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True):
            rows.append({
                'name': path.stem,
                'path': str(path),
                'bytes': path.stat().st_size,
                'updated_at': datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat(),
            })
        return rows

    def load_saved_report(self, name: str) -> dict[str, Any] | None:
        path = self.report_dir() / f"{name}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def load_calibration_profile(self) -> dict[str, Any]:
        if not self.settings.enable_calibration_profile:
            return {}
        path = self.settings.calibration_profile_path
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _json_loads(value: Any, *, fallback: Any) -> Any:
        if value in (None, ""):
            return fallback
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            _logger.warning("Failed to decode JSON field (len=%d), using fallback", len(str(value)))
            return fallback

    @staticmethod
    def _normalized_search_text(parts: list[str]) -> str:
        text = " ".join(part.strip() for part in parts if part and str(part).strip())
        return " ".join(tokenize(text, max_tokens=128)) if text else ""

    def _sync_pattern_fts(self, conn: sqlite3.Connection, pattern_id: int) -> None:
        row = conn.execute(
            """
            SELECT title, canonical_symptom, canonical_fix, prevention_rule,
                   tags, root_cause_class, error_family, domain
            FROM issue_patterns
            WHERE id = ?
            """,
            (pattern_id,),
        ).fetchone()
        if row is None:
            return
        conn.execute("DELETE FROM issue_patterns_fts WHERE rowid = ?", (pattern_id,))
        conn.execute(
            """
            INSERT INTO issue_patterns_fts(
                rowid, title, canonical_symptom, canonical_fix,
                prevention_rule, tags, root_cause_class, error_family, domain
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pattern_id,
                row["title"],
                row["canonical_symptom"],
                row["canonical_fix"],
                row["prevention_rule"],
                row["tags"],
                row["root_cause_class"],
                row["error_family"],
                row["domain"],
            ),
        )

    def _sync_example_fts(self, conn: sqlite3.Connection, example_id: int) -> None:
        row = conn.execute(
            """
            SELECT raw_error, normalized_error, context, command, file_path, verified_fix
            FROM issue_examples
            WHERE id = ?
            """,
            (example_id,),
        ).fetchone()
        if row is None:
            return
        conn.execute("DELETE FROM issue_examples_fts WHERE rowid = ?", (example_id,))
        conn.execute(
            """
            INSERT INTO issue_examples_fts(
                rowid, raw_error, normalized_error, context, command, file_path, verified_fix
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                example_id,
                row["raw_error"],
                row["normalized_error"],
                row["context"],
                row["command"],
                row["file_path"],
                row["verified_fix"],
            ),
        )

    def _scope_predicate(self, project_scope: str) -> tuple[str, list[Any], str, list[Any]]:
        if project_scope and project_scope != "global":
            return (
                "p.project_scope IN (?, 'global')",
                [project_scope],
                "CASE WHEN p.project_scope = ? THEN 0 WHEN p.project_scope = 'global' THEN 1 ELSE 2 END",
                [project_scope],
            )
        if project_scope == "global":
            return (
                "p.project_scope = 'global'",
                [],
                "CASE WHEN p.project_scope = 'global' THEN 0 ELSE 1 END",
                [],
            )
        return ("1=1", [], "CASE WHEN p.project_scope = 'global' THEN 0 ELSE 1 END", [])

    @staticmethod
    def _default_candidate_meta() -> dict[str, Any]:
        return {
            "family_rank": None,
            "root_rank": None,
            "pattern_fts_rank": None,
            "example_fts_rank": None,
            "pattern_bm25": None,
            "example_bm25": None,
        }

    @staticmethod
    def _default_variant_candidate_meta() -> dict[str, Any]:
        return {
            "family_rank": None,
            "root_rank": None,
            "variant_fts_rank": None,
            "episode_fts_rank": None,
            "pattern_fts_rank": None,
            "variant_bm25": None,
            "episode_bm25": None,
            "pattern_bm25": None,
        }

    @staticmethod
    def _raw_candidate_sort_key(meta: dict[str, Any], pattern_id: int) -> tuple[int, int, int, int, int, int, int, int, int]:
        large = 10**9
        return (
            0 if meta["root_rank"] is not None else 1,
            int(meta["root_rank"] or large),
            0 if meta["family_rank"] is not None else 1,
            int(meta["family_rank"] or large),
            0 if meta["pattern_fts_rank"] is not None else 1,
            int(meta["pattern_fts_rank"] or large),
            0 if meta["example_fts_rank"] is not None else 1,
            int(meta["example_fts_rank"] or large),
            pattern_id,
        )

    @staticmethod
    def _raw_variant_candidate_sort_key(
        meta: dict[str, Any],
        pattern_id: int,
        variant_id: int,
    ) -> tuple[int, int, int, int, int, int, int, int, int, int, int]:
        large = 10**9
        return (
            0 if meta["root_rank"] is not None else 1,
            int(meta["root_rank"] or large),
            0 if meta["family_rank"] is not None else 1,
            int(meta["family_rank"] or large),
            0 if meta["episode_fts_rank"] is not None else 1,
            int(meta["episode_fts_rank"] or large),
            0 if meta["variant_fts_rank"] is not None else 1,
            int(meta["variant_fts_rank"] or large),
            0 if meta["pattern_fts_rank"] is not None else 1,
            int(meta["pattern_fts_rank"] or large),
            pattern_id * 1000000 + variant_id,
        )

    @staticmethod
    def _percentile(values: list[int], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(int(value) for value in values)
        if len(ordered) == 1:
            return float(ordered[0])
        position = (len(ordered) - 1) * max(0.0, min(percentile, 1.0))
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        if lower == upper:
            return float(ordered[lower])
        weight = position - lower
        return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)

    def find_pattern_by_signature(self, project_scope: str, signature: str) -> dict[str, Any] | None:
        with self.managed_connection() as conn:
            row = conn.execute(
                "SELECT * FROM issue_patterns WHERE project_scope = ? AND signature = ?",
                (project_scope, signature),
            ).fetchone()
            return self._row_to_dict(row)

    def find_pattern_by_id(self, pattern_id: int) -> dict[str, Any] | None:
        with self.managed_connection() as conn:
            row = conn.execute("SELECT * FROM issue_patterns WHERE id = ?", (pattern_id,)).fetchone()
            return self._row_to_dict(row)

    def _create_pattern_tx(self, conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        try:
            cur = conn.execute(
                """
                INSERT INTO issue_patterns(
                    title, project_scope, domain, error_family, root_cause_class,
                    canonical_symptom, canonical_fix, prevention_rule, verification_steps,
                    tags, signature, times_seen, confidence, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["title"],
                    payload["project_scope"],
                    payload.get("domain", "generic"),
                    payload["error_family"],
                    payload["root_cause_class"],
                    payload["canonical_symptom"],
                    payload["canonical_fix"],
                    payload["prevention_rule"],
                    payload.get("verification_steps", ""),
                    comma_join(payload.get("tags", [])),
                    payload["signature"],
                    payload.get("times_seen", 1),
                    payload.get("confidence", 0.70),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "issue_patterns.project_scope, issue_patterns.signature" not in str(exc):
                raise
            row = conn.execute(
                "SELECT * FROM issue_patterns WHERE project_scope = ? AND signature = ?",
                (payload["project_scope"], payload["signature"]),
            ).fetchone()
            if row is None:
                raise
            return self._touch_pattern_tx(conn, int(row["id"]), payload)
        if cur.lastrowid is None:
            raise RuntimeError("Failed to determine pattern id after insert")
        pattern_id = int(cur.lastrowid)
        self._sync_pattern_fts(conn, pattern_id)
        row = conn.execute("SELECT * FROM issue_patterns WHERE id = ?", (pattern_id,)).fetchone()
        return dict(row)

    def create_pattern(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.managed_connection() as conn:
            return self._create_pattern_tx(conn, payload)

    def _touch_pattern_tx(self, conn: sqlite3.Connection, pattern_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        existing = conn.execute("SELECT * FROM issue_patterns WHERE id = ?", (pattern_id,)).fetchone()
        if existing is None:
            raise KeyError(f"Pattern {pattern_id} not found")

        existing_tags = parse_tag_string(existing["tags"])
        merged_tags = comma_join(existing_tags + list(payload.get("tags", [])))
        existing_domain = str(existing["domain"] or "generic")
        payload_domain = str(payload.get("domain", "generic") or "generic")
        if existing_domain == "generic" and payload_domain != "generic":
            domain = payload_domain
        else:
            domain = existing_domain

        title = str(existing["title"] or payload.get("title") or "").strip() or str(payload.get("title") or "").strip()
        canonical_symptom = str(existing["canonical_symptom"] or payload.get("canonical_symptom") or "").strip() or str(payload.get("canonical_symptom") or "").strip()
        canonical_fix = str(existing["canonical_fix"] or payload.get("canonical_fix") or "").strip() or str(payload.get("canonical_fix") or "").strip()
        prevention_rule = str(existing["prevention_rule"] or payload.get("prevention_rule") or "").strip() or str(payload.get("prevention_rule") or "").strip()
        verification_steps = str(existing["verification_steps"] or payload.get("verification_steps") or "").strip() or str(payload.get("verification_steps") or "").strip()
        confidence = max(float(existing["confidence"]), float(payload.get("confidence", existing["confidence"])))
        times_seen = int(existing["times_seen"]) + int(payload.get("times_seen_delta", 1))

        conn.execute(
            """
            UPDATE issue_patterns
            SET title = ?, domain = ?, tags = ?, times_seen = ?, confidence = ?,
                updated_at = ?, canonical_symptom = ?, canonical_fix = ?,
                prevention_rule = ?, verification_steps = ?
            WHERE id = ?
            """,
            (
                title,
                domain,
                merged_tags,
                times_seen,
                confidence,
                now,
                canonical_symptom,
                canonical_fix,
                prevention_rule,
                verification_steps,
                pattern_id,
            ),
        )
        self._sync_pattern_fts(conn, pattern_id)
        row = conn.execute("SELECT * FROM issue_patterns WHERE id = ?", (pattern_id,)).fetchone()
        return dict(row)

    def _add_example_tx(self, conn: sqlite3.Connection, *, pattern_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        cur = conn.execute(
            """
            INSERT INTO issue_examples(
                pattern_id, raw_error, normalized_error, context, file_path,
                command, verified_fix, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pattern_id,
                payload["raw_error"],
                payload["normalized_error"],
                payload.get("context", ""),
                payload.get("file_path", ""),
                payload.get("command", ""),
                payload.get("verified_fix", ""),
                utc_now_iso(),
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("Failed to determine example id after insert")
        example_id = int(cur.lastrowid)
        self._sync_example_fts(conn, example_id)
        row = conn.execute("SELECT * FROM issue_examples WHERE id = ?", (example_id,)).fetchone()
        return dict(row)

    def add_example(
        self,
        *,
        pattern_id: int,
        raw_error: str,
        normalized_error: str,
        context: str = "",
        file_path: str = "",
        command: str = "",
        verified_fix: str = "",
    ) -> dict[str, Any]:
        with self.managed_connection() as conn:
            return self._add_example_tx(
                conn,
                pattern_id=pattern_id,
                payload={
                    "raw_error": raw_error,
                    "normalized_error": normalized_error,
                    "context": context,
                    "file_path": file_path,
                    "command": command,
                    "verified_fix": verified_fix,
                },
            )

    def _make_variant_search_text(
        self,
        *,
        title: str,
        canonical_fix: str,
        verification_steps: str,
        tags: list[str],
        applicability: dict[str, Any],
        patch_summary: str,
        strategy_key: str = "",
        entity_slots: dict[str, Any] | None = None,
        strategy_hints: list[str] | None = None,
    ) -> str:
        entity_slots = entity_slots or {}
        strategy_hints = strategy_hints or []
        entity_text = self._json_dumps(entity_slots) if entity_slots else ""
        return self._normalized_search_text(
            [
                title,
                canonical_fix,
                verification_steps,
                " ".join(tags),
                applicability.get("project_scope", ""),
                applicability.get("error_family", ""),
                applicability.get("root_cause_class", ""),
                applicability.get("command", ""),
                applicability.get("file_path", ""),
                applicability.get("repo_name", ""),
                strategy_key,
                " ".join(strategy_hints),
                entity_text,
                patch_summary,
            ]
        )

    def _find_variant_by_key_tx(self, conn: sqlite3.Connection, *, pattern_id: int, variant_key: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM issue_variants WHERE pattern_id = ? AND variant_key = ?",
            (pattern_id, variant_key),
        ).fetchone()
        return dict(row) if row is not None else None

    def find_variant_by_key(self, *, pattern_id: int, variant_key: str) -> dict[str, Any] | None:
        with self.managed_connection() as conn:
            row = self._find_variant_by_key_tx(conn, pattern_id=pattern_id, variant_key=variant_key)
            return self._decode_variant_row(row) if row is not None else None

    def get_variants_for_pattern(self, pattern_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.managed_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM issue_variants
                WHERE pattern_id = ?
                ORDER BY confidence DESC, memory_strength DESC, updated_at DESC, id ASC
                LIMIT ?
                """,
                (pattern_id, limit),
            ).fetchall()
            return [self._decode_variant_row(row) for row in rows]

    def _create_variant_tx(self, conn: sqlite3.Connection, *, pattern_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        applicability = payload.get("applicability", {})
        negative_applicability = payload.get("negative_applicability", {})
        search_text = self._make_variant_search_text(
            title=payload.get("title", ""),
            canonical_fix=payload.get("canonical_fix", ""),
            verification_steps=payload.get("verification_steps", ""),
            tags=list(payload.get("tags", [])),
            applicability=applicability,
            patch_summary=payload.get("patch_summary", ""),
            strategy_key=str(payload.get("strategy_key", "")),
            entity_slots=payload.get("entity_slots", {}),
            strategy_hints=list(payload.get("strategy_hints", [])),
        )
        try:
            cur = conn.execute(
                """
                INSERT INTO issue_variants(
                    pattern_id, variant_key, title, applicability_json, negative_applicability_json,
                    repo_fingerprint, env_fingerprint, command_signature, file_path_signature,
                    stack_signature, patch_hash, patch_summary, canonical_fix, verification_steps,
                    rollback_steps, tags_json, search_text, strategy_key, entity_slots_json,
                    strategy_hints_json, status, times_used, success_count, reject_count,
                    confidence, memory_strength, last_used_at, last_verified_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pattern_id,
                    payload["variant_key"],
                    payload.get("title", ""),
                    self._json_dumps(applicability),
                    self._json_dumps(negative_applicability),
                    payload.get("repo_fingerprint", ""),
                    payload.get("env_fingerprint", ""),
                    payload.get("command_signature", ""),
                    payload.get("file_path_signature", ""),
                    payload.get("stack_signature", ""),
                    payload.get("patch_hash", ""),
                    payload.get("patch_summary", ""),
                    payload.get("canonical_fix", ""),
                    payload.get("verification_steps", ""),
                    payload.get("rollback_steps", ""),
                    self._json_dumps(list(payload.get("tags", []))),
                    search_text,
                    payload.get("strategy_key", ""),
                    self._json_dumps(payload.get("entity_slots", {})),
                    self._json_dumps(list(payload.get("strategy_hints", []))),
                    payload.get("status", "active"),
                    int(payload.get("times_used", 1)),
                    int(payload.get("success_count", 1)),
                    int(payload.get("reject_count", 0)),
                    float(payload.get("confidence", 0.50)),
                    float(payload.get("memory_strength", 0.50)),
                    now,
                    now,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "issue_variants.pattern_id, issue_variants.variant_key" not in str(exc):
                raise
            row = conn.execute(
                "SELECT * FROM issue_variants WHERE pattern_id = ? AND variant_key = ?",
                (pattern_id, payload["variant_key"]),
            ).fetchone()
            if row is None:
                raise
            return self._update_variant_tx(conn, variant_id=int(row["id"]), payload=payload)
        if cur.lastrowid is None:
            raise RuntimeError("Failed to determine variant id after insert")
        variant_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM issue_variants WHERE id = ?", (variant_id,)).fetchone()
        return dict(row)

    def _update_variant_tx(self, conn: sqlite3.Connection, *, variant_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        existing = conn.execute("SELECT * FROM issue_variants WHERE id = ?", (variant_id,)).fetchone()
        if existing is None:
            raise KeyError(f"Variant {variant_id} not found")
        existing_tags = self._json_loads(existing["tags_json"], fallback=[])
        if not isinstance(existing_tags, list):
            existing_tags = []
        merged_tags = list(dict.fromkeys([str(tag) for tag in existing_tags] + [str(tag) for tag in payload.get("tags", [])]))
        applicability = payload.get("applicability") or self._json_loads(existing["applicability_json"], fallback={})
        negative_applicability = payload.get("negative_applicability") or self._json_loads(existing["negative_applicability_json"], fallback={})
        entity_slots = payload.get("entity_slots") or self._json_loads(existing["entity_slots_json"], fallback={})
        strategy_hints = payload.get("strategy_hints") or self._json_loads(existing["strategy_hints_json"], fallback=[])
        status = str(payload.get("status") or existing["status"])
        if status not in {"provisional", "active", "archived"}:
            status = str(existing["status"])
        search_text = self._make_variant_search_text(
            title=str(payload.get("title") or existing["title"]),
            canonical_fix=str(payload.get("canonical_fix") or existing["canonical_fix"]),
            verification_steps=str(payload.get("verification_steps") or existing["verification_steps"]),
            tags=merged_tags,
            applicability=applicability if isinstance(applicability, dict) else {},
            patch_summary=str(payload.get("patch_summary") or existing["patch_summary"]),
            strategy_key=str(payload.get("strategy_key") or existing["strategy_key"]),
            entity_slots=entity_slots if isinstance(entity_slots, dict) else {},
            strategy_hints=list(strategy_hints) if isinstance(strategy_hints, list) else [],
        )
        conn.execute(
            """
            UPDATE issue_variants
            SET title = ?, applicability_json = ?, negative_applicability_json = ?,
                repo_fingerprint = ?, env_fingerprint = ?, command_signature = ?,
                file_path_signature = ?, stack_signature = ?, patch_hash = ?, patch_summary = ?,
                canonical_fix = ?, verification_steps = ?, rollback_steps = ?, tags_json = ?,
                search_text = ?, strategy_key = ?, entity_slots_json = ?, strategy_hints_json = ?,
                status = ?, times_used = ?, success_count = ?, confidence = ?, memory_strength = ?,
                last_used_at = ?, last_verified_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.get("title") or existing["title"],
                self._json_dumps(applicability),
                self._json_dumps(negative_applicability),
                payload.get("repo_fingerprint") or existing["repo_fingerprint"],
                payload.get("env_fingerprint") or existing["env_fingerprint"],
                payload.get("command_signature") or existing["command_signature"],
                payload.get("file_path_signature") or existing["file_path_signature"],
                payload.get("stack_signature") or existing["stack_signature"],
                payload.get("patch_hash") or existing["patch_hash"],
                payload.get("patch_summary") or existing["patch_summary"],
                payload.get("canonical_fix") or existing["canonical_fix"],
                payload.get("verification_steps") or existing["verification_steps"],
                payload.get("rollback_steps") or existing["rollback_steps"],
                self._json_dumps(merged_tags),
                search_text,
                payload.get("strategy_key") or existing["strategy_key"],
                self._json_dumps(entity_slots if isinstance(entity_slots, dict) else {}),
                self._json_dumps(list(strategy_hints) if isinstance(strategy_hints, list) else []),
                status,
                int(existing["times_used"]) + int(payload.get("times_used_delta", 1)),
                int(existing["success_count"]) + int(payload.get("success_count_delta", 1)),
                max(float(existing["confidence"]), float(payload.get("confidence", existing["confidence"]))),
                max(float(existing["memory_strength"]), float(payload.get("memory_strength", existing["memory_strength"]))),
                now,
                now,
                now,
                variant_id,
            ),
        )
        row = conn.execute("SELECT * FROM issue_variants WHERE id = ?", (variant_id,)).fetchone()
        return dict(row)

    def _make_episode_search_text(self, payload: dict[str, Any]) -> str:
        return self._normalized_search_text(
            [
                str(payload.get("raw_error", "")),
                str(payload.get("normalized_error", "")),
                str(payload.get("context", "")),
                str(payload.get("stack_excerpt", "")),
                str(payload.get("command", "")),
                str(payload.get("file_path", "")),
                str(payload.get("patch_summary", "")),
                str(payload.get("resolution_notes", "")),
                " ".join(payload.get("exception_types", [])),
                " ".join(payload.get("query_tokens", [])),
                self._json_dumps(payload.get("entity_slots", {})) if payload.get("entity_slots") else "",
                " ".join(payload.get("strategy_hints", [])),
            ]
        )

    def _insert_episode_tx(
        self,
        conn: sqlite3.Connection,
        *,
        pattern_id: int,
        variant_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now_iso()
        cur = conn.execute(
            """
            INSERT INTO issue_episodes(
                pattern_id, variant_id, session_id, project_scope, user_scope, repo_name, repo_fingerprint,
                git_commit, source, raw_error, normalized_error, context, stack_excerpt,
                command, file_path, exception_types_json, query_tokens_json, entity_slots_json,
                strategy_hints_json, env_fingerprint, env_json, patch_hash, patch_summary,
                verification_command, verification_output, outcome, consolidation_status,
                resolution_notes, search_text, created_at, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pattern_id,
                variant_id,
                payload.get("session_id", ""),
                payload.get("project_scope", "global"),
                payload.get("user_scope", ""),
                payload.get("repo_name", ""),
                payload.get("repo_fingerprint", ""),
                payload.get("git_commit", ""),
                payload.get("source", "manual"),
                payload.get("raw_error", ""),
                payload.get("normalized_error", ""),
                payload.get("context", ""),
                payload.get("stack_excerpt", ""),
                payload.get("command", ""),
                payload.get("file_path", ""),
                self._json_dumps(list(payload.get("exception_types", []))),
                self._json_dumps(list(payload.get("query_tokens", []))),
                self._json_dumps(payload.get("entity_slots", {})),
                self._json_dumps(list(payload.get("strategy_hints", []))),
                payload.get("env_fingerprint", ""),
                payload.get("env_json", "{}") or "{}",
                payload.get("patch_hash", ""),
                payload.get("patch_summary", ""),
                payload.get("verification_command", ""),
                payload.get("verification_output", ""),
                payload.get("outcome", "verified"),
                payload.get("consolidation_status", "attached"),
                payload.get("resolution_notes", ""),
                self._make_episode_search_text(payload),
                now,
                now,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("Failed to determine episode id after insert")
        episode_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM issue_episodes WHERE id = ?", (episode_id,)).fetchone()
        return dict(row)

    def record_resolution(
        self,
        *,
        matched_pattern_id: int | None,
        matched_variant_id: int | None,
        pattern_payload: dict[str, Any],
        variant_payload: dict[str, Any],
        episode_payload: dict[str, Any],
        example_payload: dict[str, Any],
        review_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.managed_connection(immediate=True) as conn:
            if matched_pattern_id is None:
                pattern = self._create_pattern_tx(conn, pattern_payload)
                pattern_action = "created"
            else:
                pattern = self._touch_pattern_tx(conn, matched_pattern_id, pattern_payload)
                pattern_action = "updated"

            pattern_id = int(pattern["id"])
            variant: dict[str, Any] | None = None
            variant_action = "created"

            if matched_variant_id is not None:
                row = conn.execute(
                    "SELECT * FROM issue_variants WHERE id = ? AND pattern_id = ?",
                    (matched_variant_id, pattern_id),
                ).fetchone()
                if row is not None:
                    variant = dict(row)

            if variant is None:
                variant = self._find_variant_by_key_tx(conn, pattern_id=pattern_id, variant_key=str(variant_payload["variant_key"]))

            if variant is None:
                variant = self._create_variant_tx(conn, pattern_id=pattern_id, payload=variant_payload)
                variant_action = "created"
            else:
                variant = self._update_variant_tx(conn, variant_id=int(variant["id"]), payload=variant_payload)
                variant_action = "updated"

            episode = self._insert_episode_tx(
                conn,
                pattern_id=pattern_id,
                variant_id=int(variant["id"]),
                payload=episode_payload,
            )
            example = self._add_example_tx(conn, pattern_id=pattern_id, payload=example_payload)
            seeded_learning = self._seed_verified_stats_tx(
                conn,
                variant_id=int(variant['id']),
                strategy_key=str(variant.get('strategy_key', '')),
                repo_name=str(episode_payload.get('repo_name', '')),
                user_scope=str(episode_payload.get('user_scope', '')),
            )
            review_item = None
            if review_payload is not None:
                review_item = self._enqueue_review_item_tx(
                    conn,
                    pattern_id=pattern_id,
                    variant_id=int(variant['id']),
                    episode_id=int(episode['id']),
                    payload=review_payload,
                )

            variant_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM issue_variants WHERE pattern_id = ?",
                    (pattern_id,),
                ).fetchone()["count"]
            )
            episode_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM issue_episodes WHERE pattern_id = ?",
                    (pattern_id,),
                ).fetchone()["count"]
            )

            return {
                "action": pattern_action,
                "pattern_action": pattern_action,
                "variant_action": variant_action,
                "pattern_id": pattern_id,
                "variant_id": int(variant["id"]),
                "episode_id": int(episode["id"]),
                "example_id": int(example["id"]),
                "times_seen": int(pattern["times_seen"]),
                "project_scope": str(pattern["project_scope"]),
                "error_family": str(pattern["error_family"]),
                "root_cause_class": str(pattern["root_cause_class"]),
                "signature": str(pattern["signature"]),
                "variant_key": str(variant["variant_key"]),
                "variant_count": variant_count,
                "episode_count": episode_count,
                "seeded_strategy_stats": seeded_learning['strategy_stat_updates'],
                "seeded_variant_stat": seeded_learning['variant_stat_update'],
                "review_item": review_item,
            }

    def _decode_variant_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["tags_json"] = self._json_loads(data.get("tags_json"), fallback=[])
        data["applicability_json"] = self._json_loads(data.get("applicability_json"), fallback={})
        data["negative_applicability_json"] = self._json_loads(data.get("negative_applicability_json"), fallback={})
        data["entity_slots_json"] = self._json_loads(data.get("entity_slots_json"), fallback={})
        data["strategy_hints_json"] = self._json_loads(data.get("strategy_hints_json"), fallback=[])
        return data

    def _decode_episode_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["exception_types_json"] = self._json_loads(data.get("exception_types_json"), fallback=[])
        data["query_tokens_json"] = self._json_loads(data.get("query_tokens_json"), fallback=[])
        data["entity_slots_json"] = self._json_loads(data.get("entity_slots_json"), fallback={})
        data["strategy_hints_json"] = self._json_loads(data.get("strategy_hints_json"), fallback=[])
        data["env_json"] = self._json_loads(data.get("env_json"), fallback={})
        return data

    def get_pattern(
        self,
        pattern_id: int,
        include_examples: bool = False,
        examples_limit: int = 10,
        *,
        include_variants: bool = True,
        variants_limit: int = 10,
        include_episodes: bool = True,
        episodes_limit: int = 10,
    ) -> PatternBundle | None:
        with self.managed_connection() as conn:
            pattern = conn.execute("SELECT * FROM issue_patterns WHERE id = ?", (pattern_id,)).fetchone()
            if pattern is None:
                return None
            examples: list[dict[str, Any]] = []
            variants: list[dict[str, Any]] = []
            episodes: list[dict[str, Any]] = []
            if include_examples:
                rows = conn.execute(
                    """
                    SELECT * FROM issue_examples
                    WHERE pattern_id = ?
                    ORDER BY created_at DESC, id ASC
                    LIMIT ?
                    """,
                    (pattern_id, examples_limit),
                ).fetchall()
                examples = [dict(row) for row in rows]
            if include_variants:
                rows = conn.execute(
                    """
                    SELECT * FROM issue_variants
                    WHERE pattern_id = ?
                    ORDER BY updated_at DESC, id ASC
                    LIMIT ?
                    """,
                    (pattern_id, variants_limit),
                ).fetchall()
                variants = [self._decode_variant_row(row) for row in rows]
            if include_episodes:
                rows = conn.execute(
                    """
                    SELECT * FROM issue_episodes
                    WHERE pattern_id = ?
                    ORDER BY created_at DESC, id ASC
                    LIMIT ?
                    """,
                    (pattern_id, episodes_limit),
                ).fetchall()
                episodes = [self._decode_episode_row(row) for row in rows]
            return PatternBundle(pattern=dict(pattern), examples=examples, variants=variants, episodes=episodes)

    def _decode_review_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["entity_slots_json"] = self._json_loads(data.get("entity_slots_json"), fallback={})
        data["metadata_json"] = self._json_loads(data.get("metadata_json"), fallback={})
        return data

    def _enqueue_review_item_tx(
        self,
        conn: sqlite3.Connection,
        *,
        pattern_id: int,
        variant_id: int,
        episode_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now_iso()
        cur = conn.execute(
            """
            INSERT INTO review_queue(
                pattern_id, variant_id, episode_id, project_scope, user_scope, repo_name,
                strategy_key, review_reason, entity_slots_json, metadata_json,
                status, resolution_note, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pattern_id,
                variant_id,
                episode_id,
                str(payload.get("project_scope", "global") or "global"),
                str(payload.get("user_scope", "") or ""),
                str(payload.get("repo_name", "") or ""),
                str(payload.get("strategy_key", "") or ""),
                str(payload.get("review_reason", "") or "review"),
                self._json_dumps(payload.get("entity_slots", {})),
                self._json_dumps(payload.get("metadata", {})),
                "pending",
                "",
                now,
                None,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("Failed to determine review queue id after insert")
        row = conn.execute("SELECT * FROM review_queue WHERE id = ?", (int(cur.lastrowid),)).fetchone()
        if row is None:
            raise RuntimeError("Review queue row missing after insert")
        return self._decode_review_row(row)

    def list_review_queue(self, *, status: str = "pending", limit: int = 20) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        normalized_status = status.strip().lower()
        if normalized_status:
            clauses.append("status = ?")
            params.append(normalized_status)
        with self.managed_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM review_queue
                WHERE {' AND '.join(clauses)}
                ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, created_at DESC, id DESC
                LIMIT ?
                """,
                [*params, max(limit, 1)],
            ).fetchall()
            return [self._decode_review_row(row) for row in rows]

    def resolve_review_item(self, *, review_id: int, decision: str, note: str = "") -> dict[str, Any] | None:
        normalized = decision.strip().lower()
        if normalized not in {"approve", "approved", "reject", "rejected", "archive", "archived", "pending"}:
            raise ValueError(f"Unsupported review decision: {decision}")
        resolved_status = {
            "approve": "approved",
            "approved": "approved",
            "reject": "rejected",
            "rejected": "rejected",
            "archive": "archived",
            "archived": "archived",
            "pending": "pending",
        }[normalized]
        now = utc_now_iso()
        with self.managed_connection(immediate=True) as conn:
            row = conn.execute("SELECT * FROM review_queue WHERE id = ?", (review_id,)).fetchone()
            if row is None:
                return None
            variant_id = int(row["variant_id"]) if row["variant_id"] is not None else None
            if variant_id is not None:
                if resolved_status == "approved":
                    conn.execute(
                        "UPDATE issue_variants SET status = 'active', updated_at = ? WHERE id = ?",
                        (now, variant_id),
                    )
                    conn.execute(
                        "UPDATE issue_episodes SET consolidation_status = 'attached', resolved_at = ? WHERE variant_id = ? AND consolidation_status = 'review'",
                        (now, variant_id),
                    )
                elif resolved_status in {"rejected", "archived"}:
                    conn.execute(
                        "UPDATE issue_variants SET status = 'archived', updated_at = ? WHERE id = ?",
                        (now, variant_id),
                    )
                    conn.execute(
                        "UPDATE issue_episodes SET consolidation_status = 'archived', resolved_at = ? WHERE variant_id = ? AND consolidation_status IN ('review', 'attached')",
                        (now, variant_id),
                    )
            conn.execute(
                """
                UPDATE review_queue
                SET status = ?, resolution_note = ?, resolved_at = ?, created_at = created_at
                WHERE id = ?
                """,
                (
                    resolved_status,
                    note,
                    None if resolved_status == "pending" else now,
                    review_id,
                ),
            )
            updated = conn.execute("SELECT * FROM review_queue WHERE id = ?", (review_id,)).fetchone()
            return self._decode_review_row(updated) if updated is not None else None

    def metrics_summary(self, *, window_days: int = 30) -> dict[str, Any]:
        window = max(int(window_days), 1)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window)).replace(microsecond=0).isoformat()
        with self.managed_connection() as conn:
            retrieval_rows = conn.execute(
                "SELECT retrieval_mode, decision_status, latency_ms FROM retrieval_events WHERE created_at >= ?",
                (cutoff,),
            ).fetchall()
            feedback_rows = conn.execute(
                "SELECT feedback_type, COUNT(*) AS count, COUNT(DISTINCT retrieval_event_id) AS retrieval_event_count FROM feedback_events WHERE created_at >= ? GROUP BY feedback_type",
                (cutoff,),
            ).fetchall()
            safe_override_count = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT retrieval_event_id) AS count FROM retrieval_candidates WHERE created_at >= ? AND reason_json LIKE ?",
                    (cutoff, '%strategy-bandit-safe-override%'),
                ).fetchone()["count"]
            )
            shadow_promote_count = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT retrieval_event_id) AS count FROM retrieval_candidates WHERE created_at >= ? AND reason_json LIKE ?",
                    (cutoff, '%strategy-bandit-shadow-promote%'),
                ).fetchone()["count"]
            )
            preference_hit_count = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT retrieval_event_id) AS count FROM retrieval_candidates WHERE created_at >= ? AND (reason_json LIKE ? OR reason_json LIKE ?)",
                    (cutoff, '%preference-rule:%', '%avoidance-rule:%'),
                ).fetchone()["count"]
            )
            pending_reviews = int(conn.execute("SELECT COUNT(*) AS count FROM review_queue WHERE status = 'pending'").fetchone()["count"])
            provisional_variants = int(conn.execute("SELECT COUNT(*) AS count FROM issue_variants WHERE status = 'provisional'").fetchone()["count"])
            archived_variants = int(conn.execute("SELECT COUNT(*) AS count FROM issue_variants WHERE status = 'archived'").fetchone()["count"])
            total_patterns = int(conn.execute("SELECT COUNT(*) AS count FROM issue_patterns").fetchone()["count"])
            total_variants = int(conn.execute("SELECT COUNT(*) AS count FROM issue_variants").fetchone()["count"])
        decision_counts: dict[str, int] = {"match": 0, "ambiguous": 0, "abstain": 0}
        mode_counts: dict[str, int] = {}
        latencies: list[int] = []
        for row in retrieval_rows:
            mode = str(row["retrieval_mode"])
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
            status = str(row["decision_status"])
            decision_counts[status] = decision_counts.get(status, 0) + 1
            latencies.append(int(row["latency_ms"] or 0))
        feedback_counts = {str(row["feedback_type"]): int(row["count"]) for row in feedback_rows}
        feedback_event_counts = {str(row["feedback_type"]): int(row["retrieval_event_count"]) for row in feedback_rows}
        visible_match_events = max(decision_counts.get("match", 0) + decision_counts.get("ambiguous", 0), 1)
        backup_files = sorted(self.settings.backup_dir.glob("issue_memory_*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True)
        latest_backup = backup_files[0] if backup_files else None
        latest_backup_age_hours = None
        if latest_backup is not None:
            latest_backup_dt = datetime.fromtimestamp(latest_backup.stat().st_mtime, tz=timezone.utc)
            latest_backup_age_hours = round((datetime.now(timezone.utc) - latest_backup_dt).total_seconds() / 3600.0, 3)
        calibration_profile = self.load_calibration_profile()
        server_status = read_server_lifecycle_status(self.settings).to_dict()
        reports = self.list_saved_reports()
        return {
            "window_days": window,
            "database": {
                "path": str(self.settings.db_path),
                "bytes": self.settings.db_path.stat().st_size if self.settings.db_path.exists() else 0,
                "patterns": total_patterns,
                "variants": total_variants,
                "provisional_variants": provisional_variants,
                "archived_variants": archived_variants,
            },
            "retrieval": {
                "total": len(retrieval_rows),
                "by_mode": mode_counts,
                "decision_counts": decision_counts,
                "decision_rates": {
                    key: round(count / max(len(retrieval_rows), 1), 6)
                    for key, count in decision_counts.items()
                },
                "latency_ms": {
                    "p50": round(self._percentile(latencies, 0.50), 3),
                    "p95": round(self._percentile(latencies, 0.95), 3),
                    "p99": round(self._percentile(latencies, 0.99), 3),
                },
                "safe_override_count": safe_override_count,
                "safe_override_rate": round(safe_override_count / visible_match_events, 6),
                "shadow_promote_count": shadow_promote_count,
                "shadow_promote_rate": round(shadow_promote_count / visible_match_events, 6),
                "preference_rule_hit_count": preference_hit_count,
            },
            "feedback": {
                "counts": feedback_counts,
                "verified_fix_conversion_rate": round(feedback_event_counts.get('fix_verified', 0) / visible_match_events, 6),
                "false_positive_rate": round(feedback_event_counts.get('false_positive', 0) / visible_match_events, 6),
            },
            "review_queue": {
                "pending": pending_reviews,
            },
            "backups": {
                "latest_path": str(latest_backup) if latest_backup is not None else None,
                "latest_age_hours": latest_backup_age_hours,
            },
            "server": server_status,
            "calibration": {
                "enabled": bool(self.settings.enable_calibration_profile),
                "profile_path": str(self.settings.calibration_profile_path),
                "has_profile": bool(calibration_profile),
                "profile_version": calibration_profile.get('version') if isinstance(calibration_profile, dict) else None,
                "profile_updated_at": calibration_profile.get('generated_at') if isinstance(calibration_profile, dict) else None,
            },
            "reports": reports,
        }

    def prune_operational_data(
        self,
        *,
        telemetry_retention_days: int | None = None,
        resolved_review_retention_days: int | None = None,
    ) -> dict[str, int]:
        telemetry_days = telemetry_retention_days or self.settings.telemetry_retention_days
        review_days = resolved_review_retention_days or self.settings.resolved_review_retention_days
        telemetry_cutoff = (datetime.now(timezone.utc) - timedelta(days=max(int(telemetry_days), 1))).replace(microsecond=0).isoformat()
        review_cutoff = (datetime.now(timezone.utc) - timedelta(days=max(int(review_days), 1))).replace(microsecond=0).isoformat()
        with self.managed_connection(immediate=True) as conn:
            expired_session_rows = self._purge_expired_session_memory_tx(conn)
            retrieval_count = int(conn.execute("SELECT COUNT(*) AS count FROM retrieval_events WHERE created_at < ?", (telemetry_cutoff,)).fetchone()["count"])
            conn.execute("DELETE FROM retrieval_events WHERE created_at < ?", (telemetry_cutoff,))
            review_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM review_queue WHERE status != 'pending' AND COALESCE(resolved_at, created_at) < ?",
                    (review_cutoff,),
                ).fetchone()["count"]
            )
            conn.execute(
                "DELETE FROM review_queue WHERE status != 'pending' AND COALESCE(resolved_at, created_at) < ?",
                (review_cutoff,),
            )
        return {
            "expired_session_memory_deleted": expired_session_rows,
            "retrieval_events_deleted": retrieval_count,
            "resolved_reviews_deleted": review_count,
        }

    def recent_patterns(self, *, limit: int = 5, project_scope: str = "") -> list[dict[str, Any]]:
        with self.managed_connection() as conn:
            if project_scope:
                rows = conn.execute(
                    """
                    SELECT * FROM issue_patterns
                    WHERE project_scope = ?
                    ORDER BY updated_at DESC, id ASC
                    LIMIT ?
                    """,
                    (project_scope, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM issue_patterns ORDER BY updated_at DESC, id ASC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    def pattern_candidates(
        self,
        *,
        fts_query: str,
        project_scope: str,
        error_family: str,
        root_cause_class: str,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Return deterministic hybrid candidates without unrelated fallback."""
        candidate_meta: dict[int, dict[str, Any]] = {}

        def _register(rows: list[sqlite3.Row], rank_key: str, bm25_key: str | None = None) -> None:
            for rank, row in enumerate(rows, start=1):
                pattern_id = int(row["id"])
                meta = candidate_meta.setdefault(pattern_id, self._default_candidate_meta())
                if meta[rank_key] is None:
                    meta[rank_key] = rank
                if bm25_key and bm25_key in row.keys() and meta[bm25_key] is None:
                    meta[bm25_key] = float(row[bm25_key])

        with self.managed_connection() as conn:
            scope_predicate, scope_predicate_args, scope_order, scope_order_args = self._scope_predicate(project_scope)

            if error_family and error_family != "generic_runtime_error":
                rows = conn.execute(
                    f"""
                    SELECT p.id
                    FROM issue_patterns p
                    WHERE p.error_family = ? AND {scope_predicate}
                    ORDER BY {scope_order}, p.updated_at DESC, p.id ASC
                    LIMIT ?
                    """,
                    [error_family, *scope_predicate_args, *scope_order_args, limit],
                ).fetchall()
                _register(rows, "family_rank")

            if root_cause_class and root_cause_class != "unknown":
                rows = conn.execute(
                    f"""
                    SELECT p.id
                    FROM issue_patterns p
                    WHERE p.root_cause_class = ? AND {scope_predicate}
                    ORDER BY {scope_order}, p.updated_at DESC, p.id ASC
                    LIMIT ?
                    """,
                    [root_cause_class, *scope_predicate_args, *scope_order_args, limit],
                ).fetchall()
                _register(rows, "root_rank")

            if fts_query:
                rows = conn.execute(
                    f"""
                    SELECT p.id, bm25(issue_patterns_fts) AS pattern_bm25
                    FROM issue_patterns_fts
                    JOIN issue_patterns p ON p.id = issue_patterns_fts.rowid
                    WHERE issue_patterns_fts MATCH ? AND {scope_predicate}
                    ORDER BY bm25(issue_patterns_fts), {scope_order}, p.updated_at DESC, p.id ASC
                    LIMIT ?
                    """,
                    [fts_query, *scope_predicate_args, *scope_order_args, limit],
                ).fetchall()
                _register(rows, "pattern_fts_rank", "pattern_bm25")

                rows = conn.execute(
                    f"""
                    SELECT e.pattern_id AS id, bm25(issue_examples_fts) AS example_bm25
                    FROM issue_examples_fts
                    JOIN issue_examples e ON e.id = issue_examples_fts.rowid
                    JOIN issue_patterns p ON p.id = e.pattern_id
                    WHERE issue_examples_fts MATCH ? AND {scope_predicate}
                    ORDER BY bm25(issue_examples_fts), {scope_order}, e.created_at DESC, e.id ASC
                    LIMIT ?
                    """,
                    [fts_query, *scope_predicate_args, *scope_order_args, max(limit * 3, limit)],
                ).fetchall()
                _register(rows, "example_fts_rank", "example_bm25")

            if not candidate_meta:
                return []

            ordered_ids = sorted(candidate_meta, key=lambda pattern_id: self._raw_candidate_sort_key(candidate_meta[pattern_id], pattern_id))[:limit]
            placeholders = ", ".join(["?"] * len(ordered_ids))
            pattern_rows = conn.execute(
                f"SELECT * FROM issue_patterns WHERE id IN ({placeholders})",
                ordered_ids,
            ).fetchall()
            rows_by_id = {int(row["id"]): dict(row) for row in pattern_rows}

            example_rows = conn.execute(
                f"""
                SELECT * FROM issue_examples
                WHERE pattern_id IN ({placeholders})
                ORDER BY pattern_id ASC, created_at DESC, id ASC
                """,
                ordered_ids,
            ).fetchall()

        examples_by_pattern: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in example_rows:
            row_dict = dict(row)
            pattern_id = int(row_dict["pattern_id"])
            if len(examples_by_pattern[pattern_id]) < 3:
                examples_by_pattern[pattern_id].append(row_dict)

        results: list[dict[str, Any]] = []
        for raw_rank, pattern_id in enumerate(ordered_ids, start=1):
            if pattern_id not in rows_by_id:
                continue
            meta = dict(candidate_meta[pattern_id])
            meta["raw_rank"] = raw_rank
            results.append(
                {
                    **rows_by_id[pattern_id],
                    "examples": examples_by_pattern.get(pattern_id, []),
                    "retrieval_signals": meta,
                }
            )
        return results

    def variant_candidates(
        self,
        *,
        fts_query: str,
        project_scope: str,
        error_family: str,
        root_cause_class: str,
        repo_fingerprint: str = "",
        env_fingerprint: str = "",
        command_signature: str = "",
        path_signature: str = "",
        stack_signature: str = "",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Return hybrid variant candidates backed by pattern, variant and episode evidence."""
        candidate_meta: dict[tuple[int, int], dict[str, Any]] = {}

        def _register(rows: list[sqlite3.Row], rank_key: str, bm25_key: str | None = None) -> None:
            for rank, row in enumerate(rows, start=1):
                pattern_id = int(row["pattern_id"])
                variant_id = int(row["variant_id"])
                meta = candidate_meta.setdefault((pattern_id, variant_id), self._default_variant_candidate_meta())
                if meta[rank_key] is None:
                    meta[rank_key] = rank
                if bm25_key and bm25_key in row.keys() and meta[bm25_key] is None:
                    meta[bm25_key] = float(row[bm25_key])

        with self.managed_connection() as conn:
            scope_predicate, scope_predicate_args, scope_order, scope_order_args = self._scope_predicate(project_scope)

            if error_family and error_family != "generic_runtime_error":
                rows = conn.execute(
                    f"""
                    SELECT p.id AS pattern_id, v.id AS variant_id
                    FROM issue_variants v
                    JOIN issue_patterns p ON p.id = v.pattern_id
                    WHERE p.error_family = ? AND v.status = 'active' AND {scope_predicate}
                    ORDER BY {scope_order}, v.confidence DESC, v.updated_at DESC, v.id ASC
                    LIMIT ?
                    """,
                    [error_family, *scope_predicate_args, *scope_order_args, limit],
                ).fetchall()
                _register(rows, "family_rank")

            if root_cause_class and root_cause_class != "unknown":
                rows = conn.execute(
                    f"""
                    SELECT p.id AS pattern_id, v.id AS variant_id
                    FROM issue_variants v
                    JOIN issue_patterns p ON p.id = v.pattern_id
                    WHERE p.root_cause_class = ? AND v.status = 'active' AND {scope_predicate}
                    ORDER BY {scope_order}, v.confidence DESC, v.updated_at DESC, v.id ASC
                    LIMIT ?
                    """,
                    [root_cause_class, *scope_predicate_args, *scope_order_args, limit],
                ).fetchall()
                _register(rows, "root_rank")

            if fts_query:
                rows = conn.execute(
                    f"""
                    SELECT p.id AS pattern_id, v.id AS variant_id, bm25(issue_variants_fts) AS variant_bm25
                    FROM issue_variants_fts
                    JOIN issue_variants v ON v.id = issue_variants_fts.rowid
                    JOIN issue_patterns p ON p.id = v.pattern_id
                    WHERE issue_variants_fts MATCH ? AND v.status = 'active' AND {scope_predicate}
                    ORDER BY bm25(issue_variants_fts), {scope_order}, v.updated_at DESC, v.id ASC
                    LIMIT ?
                    """,
                    [fts_query, *scope_predicate_args, *scope_order_args, max(limit * 2, limit)],
                ).fetchall()
                _register(rows, "variant_fts_rank", "variant_bm25")

                rows = conn.execute(
                    f"""
                    SELECT p.id AS pattern_id, v.id AS variant_id, bm25(issue_episodes_fts) AS episode_bm25
                    FROM issue_episodes_fts
                    JOIN issue_episodes e ON e.id = issue_episodes_fts.rowid
                    JOIN issue_variants v ON v.id = e.variant_id
                    JOIN issue_patterns p ON p.id = v.pattern_id
                    WHERE issue_episodes_fts MATCH ? AND v.status = 'active' AND {scope_predicate}
                    ORDER BY bm25(issue_episodes_fts), {scope_order}, e.resolved_at DESC, e.id ASC
                    LIMIT ?
                    """,
                    [fts_query, *scope_predicate_args, *scope_order_args, max(limit * 3, limit)],
                ).fetchall()
                _register(rows, "episode_fts_rank", "episode_bm25")

                rows = conn.execute(
                    f"""
                    SELECT p.id AS pattern_id, v.id AS variant_id, bm25(issue_patterns_fts) AS pattern_bm25
                    FROM issue_patterns_fts
                    JOIN issue_patterns p ON p.id = issue_patterns_fts.rowid
                    JOIN issue_variants v ON v.pattern_id = p.id
                    WHERE issue_patterns_fts MATCH ? AND v.status = 'active' AND {scope_predicate}
                    ORDER BY bm25(issue_patterns_fts), {scope_order}, v.confidence DESC, v.updated_at DESC, v.id ASC
                    LIMIT ?
                    """,
                    [fts_query, *scope_predicate_args, *scope_order_args, max(limit * 2, limit)],
                ).fetchall()
                _register(rows, "pattern_fts_rank", "pattern_bm25")

            if not candidate_meta:
                return []

            ordered_pairs = sorted(
                candidate_meta,
                key=lambda pair: self._raw_variant_candidate_sort_key(candidate_meta[pair], pair[0], pair[1]),
            )[:limit]
            variant_ids = [variant_id for _pattern_id, variant_id in ordered_pairs]
            pattern_ids = list(dict.fromkeys(pattern_id for pattern_id, _variant_id in ordered_pairs))
            variant_placeholders = ", ".join(["?"] * len(variant_ids))
            pattern_placeholders = ", ".join(["?"] * len(pattern_ids))

            variant_rows = conn.execute(
                f"""
                SELECT
                    p.id AS pattern_id,
                    p.title AS pattern_title,
                    p.project_scope AS pattern_project_scope,
                    p.domain AS pattern_domain,
                    p.error_family AS pattern_error_family,
                    p.root_cause_class AS pattern_root_cause_class,
                    p.canonical_symptom AS pattern_canonical_symptom,
                    p.canonical_fix AS pattern_canonical_fix,
                    p.prevention_rule AS pattern_prevention_rule,
                    p.verification_steps AS pattern_verification_steps,
                    p.tags AS pattern_tags,
                    p.signature AS pattern_signature,
                    p.times_seen AS pattern_times_seen,
                    p.confidence AS pattern_confidence,
                    p.created_at AS pattern_created_at,
                    p.updated_at AS pattern_updated_at,
                    v.id AS variant_id,
                    v.variant_key,
                    v.title AS variant_title,
                    v.applicability_json,
                    v.negative_applicability_json,
                    v.repo_fingerprint,
                    v.env_fingerprint,
                    v.command_signature,
                    v.file_path_signature,
                    v.stack_signature,
                    v.patch_hash,
                    v.patch_summary,
                    v.canonical_fix AS variant_canonical_fix,
                    v.verification_steps AS variant_verification_steps,
                    v.rollback_steps,
                    v.tags_json,
                    v.search_text AS variant_search_text,
                    v.strategy_key AS variant_strategy_key,
                    v.entity_slots_json AS variant_entity_slots_json,
                    v.strategy_hints_json AS variant_strategy_hints_json,
                    v.status AS variant_status,
                    v.times_used,
                    v.success_count,
                    v.reject_count,
                    v.confidence AS variant_confidence,
                    v.memory_strength,
                    v.last_used_at,
                    v.last_verified_at,
                    v.created_at AS variant_created_at,
                    v.updated_at AS variant_updated_at
                FROM issue_variants v
                JOIN issue_patterns p ON p.id = v.pattern_id
                WHERE v.id IN ({variant_placeholders})
                """,
                variant_ids,
            ).fetchall()

            episode_rows = conn.execute(
                f"""
                SELECT * FROM issue_episodes
                WHERE variant_id IN ({variant_placeholders})
                ORDER BY variant_id ASC, resolved_at DESC, id ASC
                """,
                variant_ids,
            ).fetchall()

            example_rows = conn.execute(
                f"""
                SELECT * FROM issue_examples
                WHERE pattern_id IN ({pattern_placeholders})
                ORDER BY pattern_id ASC, created_at DESC, id ASC
                """,
                pattern_ids,
            ).fetchall()

        rows_by_variant: dict[int, dict[str, Any]] = {int(row["variant_id"]): dict(row) for row in variant_rows}
        examples_by_pattern: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in example_rows:
            row_dict = dict(row)
            pattern_id = int(row_dict["pattern_id"])
            if len(examples_by_pattern[pattern_id]) < 3:
                examples_by_pattern[pattern_id].append(row_dict)

        episodes_by_variant: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in episode_rows:
            episode = self._decode_episode_row(row)
            variant_id = int(episode["variant_id"])
            if len(episodes_by_variant[variant_id]) < 3:
                episodes_by_variant[variant_id].append(episode)

        results: list[dict[str, Any]] = []
        for raw_rank, (pattern_id, variant_id) in enumerate(ordered_pairs, start=1):
            row = rows_by_variant.get(variant_id)
            if row is None:
                continue
            variant = {
                "id": int(row["variant_id"]),
                "pattern_id": int(row["pattern_id"]),
                "variant_key": str(row["variant_key"]),
                "title": str(row["variant_title"]),
                "applicability_json": self._json_loads(row.get("applicability_json"), fallback={}),
                "negative_applicability_json": self._json_loads(row.get("negative_applicability_json"), fallback={}),
                "repo_fingerprint": str(row.get("repo_fingerprint", "")),
                "env_fingerprint": str(row.get("env_fingerprint", "")),
                "command_signature": str(row.get("command_signature", "")),
                "file_path_signature": str(row.get("file_path_signature", "")),
                "stack_signature": str(row.get("stack_signature", "")),
                "patch_hash": str(row.get("patch_hash", "")),
                "patch_summary": str(row.get("patch_summary", "")),
                "canonical_fix": str(row.get("variant_canonical_fix", "")),
                "verification_steps": str(row.get("variant_verification_steps", "")),
                "rollback_steps": str(row.get("rollback_steps", "")),
                "tags_json": self._json_loads(row.get("tags_json"), fallback=[]),
                "search_text": str(row.get("variant_search_text", "")),
                "strategy_key": str(row.get("variant_strategy_key", "")),
                "entity_slots_json": self._json_loads(row.get("variant_entity_slots_json"), fallback={}),
                "strategy_hints_json": self._json_loads(row.get("variant_strategy_hints_json"), fallback=[]),
                "status": str(row.get("variant_status", "active")),
                "times_used": int(row.get("times_used", 0)),
                "success_count": int(row.get("success_count", 0)),
                "reject_count": int(row.get("reject_count", 0)),
                "confidence": float(row.get("variant_confidence", 0.0)),
                "memory_strength": float(row.get("memory_strength", 0.0)),
                "last_used_at": str(row.get("last_used_at", "")),
                "last_verified_at": str(row.get("last_verified_at", "")),
                "created_at": str(row.get("variant_created_at", "")),
                "updated_at": str(row.get("variant_updated_at", "")),
            }
            variant_match_score = self._variant_context_score(
                variant,
                repo_fingerprint=repo_fingerprint,
                env_fingerprint=env_fingerprint,
                command_signature=command_signature,
                path_signature=path_signature,
                stack_signature=stack_signature,
            )
            meta = dict(candidate_meta[(pattern_id, variant_id)])
            meta["raw_rank"] = raw_rank
            results.append(
                {
                    "id": pattern_id,
                    "pattern_id": pattern_id,
                    "variant_id": variant_id,
                    "candidate_type": "variant",
                    "title": str(row["pattern_title"]),
                    "project_scope": str(row["pattern_project_scope"]),
                    "domain": str(row["pattern_domain"]),
                    "error_family": str(row["pattern_error_family"]),
                    "root_cause_class": str(row["pattern_root_cause_class"]),
                    "canonical_symptom": str(row["pattern_canonical_symptom"]),
                    "canonical_fix": str(row["pattern_canonical_fix"]),
                    "prevention_rule": str(row["pattern_prevention_rule"]),
                    "verification_steps": str(row["pattern_verification_steps"]),
                    "tags": str(row["pattern_tags"]),
                    "signature": str(row["pattern_signature"]),
                    "times_seen": int(row["pattern_times_seen"]),
                    "confidence": float(row["pattern_confidence"]),
                    "created_at": str(row["pattern_created_at"]),
                    "updated_at": str(row["pattern_updated_at"]),
                    "best_variant": variant,
                    "variant_match_score": round(min(max(variant_match_score, 0.0), 1.0), 6),
                    "episodes": episodes_by_variant.get(variant_id, []),
                    "examples": examples_by_pattern.get(pattern_id, []),
                    "retrieval_signals": meta,
                }
            )
        return results

    @staticmethod
    def _posterior_mean(alpha: float, beta: float) -> float:
        total = float(alpha) + float(beta)
        if total <= 0.0:
            return 0.5
        return float(alpha) / total

    def _decode_strategy_stat_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        alpha = float(data.get('alpha', STRATEGY_PRIOR_ALPHA))
        beta = float(data.get('beta', STRATEGY_PRIOR_BETA))
        data['alpha'] = alpha
        data['beta'] = beta
        data['success_count'] = int(data.get('success_count', 0))
        data['failure_count'] = int(data.get('failure_count', 0))
        data['posterior_mean'] = self._posterior_mean(alpha, beta)
        data['effective_observations'] = max(alpha + beta - STRATEGY_PRIOR_ALPHA - STRATEGY_PRIOR_BETA, 0.0)
        return data

    def _decode_variant_stat_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        alpha = float(data.get('alpha', VARIANT_PRIOR_ALPHA))
        beta = float(data.get('beta', VARIANT_PRIOR_BETA))
        data['alpha'] = alpha
        data['beta'] = beta
        data['success_count'] = int(data.get('success_count', 0))
        data['failure_count'] = int(data.get('failure_count', 0))
        data['posterior_mean'] = self._posterior_mean(alpha, beta)
        data['effective_observations'] = max(alpha + beta - VARIANT_PRIOR_ALPHA - VARIANT_PRIOR_BETA, 0.0)
        return data

    def get_strategy_stat(self, *, scope_type: str, scope_key: str, strategy_key: str) -> dict[str, Any] | None:
        normalized_scope_key = '' if scope_type == 'global' else scope_key.strip()
        with self.managed_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM strategy_stats
                WHERE scope_type = ? AND scope_key = ? AND strategy_key = ?
                """,
                (scope_type, normalized_scope_key, strategy_key),
            ).fetchone()
            return self._decode_strategy_stat_row(row) if row is not None else None

    def get_variant_stat(self, variant_id: int) -> dict[str, Any] | None:
        with self.managed_connection() as conn:
            row = conn.execute(
                "SELECT * FROM variant_stats WHERE variant_id = ?",
                (variant_id,),
            ).fetchone()
            return self._decode_variant_stat_row(row) if row is not None else None

    def load_strategy_bandit_stats(
        self,
        *,
        strategy_keys: list[str],
        variant_ids: list[int],
        repo_name: str = "",
        user_scope: str = "",
    ) -> dict[str, Any]:
        normalized_strategy_keys = list(dict.fromkeys(key.strip() for key in strategy_keys if key.strip()))
        normalized_variant_ids = list(dict.fromkeys(int(variant_id) for variant_id in variant_ids if int(variant_id) > 0))
        result: dict[str, Any] = {
            "global": {},
            "repo": {},
            "user": {},
            "variants": {},
        }
        with self.managed_connection() as conn:
            if normalized_strategy_keys:
                placeholders = ", ".join(["?"] * len(normalized_strategy_keys))
                for scope_type, scope_key in self._strategy_scope_entries(repo_name=repo_name, user_scope=user_scope):
                    normalized_scope_key = self._normalized_scope_key(scope_type, scope_key)
                    rows = conn.execute(
                        f"""
                        SELECT * FROM strategy_stats
                        WHERE scope_type = ? AND scope_key = ? AND strategy_key IN ({placeholders})
                        """,
                        [scope_type, normalized_scope_key, *normalized_strategy_keys],
                    ).fetchall()
                    result[scope_type] = {
                        str(row["strategy_key"]): self._decode_strategy_stat_row(row)
                        for row in rows
                    }
            if normalized_variant_ids:
                placeholders = ", ".join(["?"] * len(normalized_variant_ids))
                rows = conn.execute(
                    f"SELECT * FROM variant_stats WHERE variant_id IN ({placeholders})",
                    normalized_variant_ids,
                ).fetchall()
                result["variants"] = {
                    int(row["variant_id"]): self._decode_variant_stat_row(row)
                    for row in rows
                }
        return result

    def _decode_preference_rule_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["weight"] = float(data.get("weight", 0.0))
        data["active"] = bool(int(data.get("active", 1)))
        data["condition_json"] = self._json_loads(data.get("condition_json"), fallback={})
        return data

    @staticmethod
    def _token_overlap_fraction(left: list[str], right: list[str]) -> float:
        if not left or not right:
            return 0.0
        lset = {str(token).strip().lower() for token in left if str(token).strip()}
        rset = {str(token).strip().lower() for token in right if str(token).strip()}
        if not lset or not rset:
            return 0.0
        overlap = lset & rset
        if not overlap:
            return 0.0
        return len(overlap) / max(len(rset), 1)

    def _preference_condition_match_score(
        self,
        condition: dict[str, Any],
        *,
        profile: Any,
        repo_name: str,
    ) -> float:
        if not isinstance(condition, dict) or not condition:
            return 1.0

        score = 1.0
        expected_repo = str(condition.get("repo_name", "")).strip().lower()
        current_repo = repo_name.strip().lower()
        if expected_repo:
            if not current_repo or current_repo != expected_repo:
                return 0.0
            score += 0.18

        command_tokens = [str(token).strip().lower() for token in condition.get("command_tokens", []) if str(token).strip()]
        if command_tokens:
            profile_command_tokens = [str(token).strip().lower() for token in getattr(profile, "command_tokens", []) if str(token).strip()]
            overlap = self._token_overlap_fraction(profile_command_tokens, command_tokens)
            if overlap <= 0.0:
                return 0.0
            score += 0.14 * overlap

        path_tokens = [str(token).strip().lower() for token in condition.get("path_tokens", []) if str(token).strip()]
        if path_tokens:
            profile_path_tokens = [str(token).strip().lower() for token in getattr(profile, "path_tokens", []) if str(token).strip()]
            overlap = self._token_overlap_fraction(profile_path_tokens, path_tokens)
            if overlap <= 0.0:
                return 0.0
            score += 0.14 * overlap

        strategy_hints = [str(token).strip().lower() for token in condition.get("strategy_hints", []) if str(token).strip()]
        if strategy_hints:
            profile_hints = [str(token).strip().lower() for token in getattr(profile, "strategy_hints", []) if str(token).strip()]
            overlap = self._token_overlap_fraction(profile_hints, strategy_hints)
            if overlap > 0.0:
                score += 0.10 * overlap

        return min(score, 1.45)

    def upsert_preference_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope_type = str(payload.get("scope_type", "global")).strip() or "global"
        if scope_type not in {"global", "repo", "user"}:
            raise ValueError(f"Unsupported preference scope_type: {scope_type}")
        scope_key = self._normalized_scope_key(scope_type, str(payload.get("scope_key", "")))
        project_scope = str(payload.get("project_scope", "global")).strip() or "global"
        repo_name = str(payload.get("repo_name", "")).strip()
        error_family = str(payload.get("error_family", "")).strip()
        strategy_key = str(payload.get("strategy_key", "")).strip()
        instruction = str(payload.get("instruction", "")).strip()
        if not instruction:
            raise ValueError("Preference instruction must not be empty")
        weight = float(payload.get("weight", 0.12))
        source = str(payload.get("source", "user_prompt")).strip() or "user_prompt"
        condition = payload.get("condition", {})
        active = 1 if bool(payload.get("active", True)) else 0
        now = utc_now_iso()

        with self.managed_connection(immediate=True) as conn:
            existing = conn.execute(
                """
                SELECT id FROM preference_rules
                WHERE scope_type = ? AND scope_key = ? AND project_scope = ? AND repo_name = ?
                  AND error_family = ? AND strategy_key = ? AND instruction = ? AND source = ?
                """,
                (scope_type, scope_key, project_scope, repo_name, error_family, strategy_key, instruction, source),
            ).fetchone()
            if existing is None:
                cur = conn.execute(
                    """
                    INSERT INTO preference_rules(
                        scope_type, scope_key, project_scope, repo_name, error_family, strategy_key,
                        weight, instruction, condition_json, source, active, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scope_type,
                        scope_key,
                        project_scope,
                        repo_name,
                        error_family,
                        strategy_key,
                        weight,
                        instruction,
                        self._json_dumps(condition),
                        source,
                        active,
                        now,
                        now,
                    ),
                )
                if cur.lastrowid is None:
                    raise RuntimeError("Failed to determine preference rule id after insert")
                rule_id = int(cur.lastrowid)
            else:
                rule_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE preference_rules
                    SET weight = ?, condition_json = ?, active = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (weight, self._json_dumps(condition), active, now, rule_id),
                )
            row = conn.execute("SELECT * FROM preference_rules WHERE id = ?", (rule_id,)).fetchone()
            if row is None:
                raise RuntimeError("Preference rule row could not be loaded after upsert")
            return self._decode_preference_rule_row(row)

    def list_preference_rules(
        self,
        *,
        scope_type: str = "",
        scope_key: str = "",
        project_scope: str = "",
        repo_name: str = "",
        active_only: bool = True,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if active_only:
            clauses.append("active = 1")
        if scope_type:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        if scope_key:
            clauses.append("scope_key = ?")
            params.append(self._normalized_scope_key(scope_type or "user", scope_key))
        if project_scope:
            clauses.append("project_scope = ?")
            params.append(project_scope.strip() or "global")
        if repo_name:
            clauses.append("repo_name = ?")
            params.append(repo_name.strip())
        with self.managed_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM preference_rules
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE scope_type WHEN 'user' THEN 0 WHEN 'repo' THEN 1 ELSE 2 END,
                    ABS(weight) DESC,
                    updated_at DESC
                LIMIT ?
                """,
                [*params, max(limit, 1)],
            ).fetchall()
            return [self._decode_preference_rule_row(row) for row in rows]

    def load_matching_preference_rules(
        self,
        *,
        profile: Any,
        project_scope: str,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        normalized_project_scope = project_scope.strip() or str(getattr(profile, "project_scope", "global") or "global")
        repo_name = str(getattr(profile, "repo_name", "") or "").strip()
        user_scope = str(getattr(profile, "user_scope", "") or "").strip()
        error_family = str(getattr(profile, "error_family", "") or "").strip()

        scope_clauses = ["(scope_type = 'global' AND scope_key = '')"]
        params: list[Any] = []
        if repo_name:
            scope_clauses.append("(scope_type = 'repo' AND scope_key = ?)")
            params.append(repo_name)
        if user_scope:
            scope_clauses.append("(scope_type = 'user' AND scope_key = ?)")
            params.append(user_scope)

        clauses = [
            "active = 1",
            "(" + " OR ".join(scope_clauses) + ")",
            "(project_scope = ? OR project_scope = 'global')",
        ]
        params.append(normalized_project_scope)

        if repo_name:
            clauses.append("(repo_name = '' OR repo_name = ?)")
            params.append(repo_name)
        else:
            clauses.append("repo_name = ''")

        if error_family and error_family != "generic_runtime_error":
            clauses.append("(error_family = '' OR error_family = ?)")
            params.append(error_family)

        with self.managed_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM preference_rules
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE scope_type WHEN 'user' THEN 0 WHEN 'repo' THEN 1 ELSE 2 END,
                    CASE WHEN project_scope = ? THEN 0 WHEN project_scope = 'global' THEN 1 ELSE 2 END,
                    ABS(weight) DESC,
                    updated_at DESC
                LIMIT ?
                """,
                [*params, normalized_project_scope, max(limit * 4, 24)],
            ).fetchall()

        scoped_weight = {"user": 1.0, "repo": 0.92, "global": 0.82}
        matched: list[dict[str, Any]] = []
        for row in rows:
            data = self._decode_preference_rule_row(row)
            project_weight = 1.0 if str(data.get("project_scope", "global")) == normalized_project_scope else 0.86
            family_value = str(data.get("error_family", "")).strip()
            if family_value and family_value == error_family and error_family:
                family_weight = 1.0
            elif not family_value:
                family_weight = 0.78
            elif error_family == "generic_runtime_error":
                family_weight = 0.72
            elif family_value == "generic_runtime_error" and error_family == "generic_runtime_error":
                family_weight = 0.88
            else:
                family_weight = 0.0
            if family_weight <= 0.0:
                continue
            condition_score = self._preference_condition_match_score(
                data.get("condition_json", {}),
                profile=profile,
                repo_name=repo_name,
            )
            if condition_score <= 0.0:
                continue
            scope_weight = scoped_weight.get(str(data.get("scope_type", "global")), 0.8)
            match_score = scope_weight * project_weight * family_weight * condition_score
            data["scope_weight"] = round(scope_weight, 6)
            data["project_weight"] = round(project_weight, 6)
            data["family_weight"] = round(family_weight, 6)
            data["condition_match_score"] = round(condition_score, 6)
            data["match_score"] = round(match_score, 6)
            matched.append(data)

        matched.sort(
            key=lambda item: (
                -abs(float(item.get("weight", 0.0)) * float(item.get("match_score", 0.0))),
                0 if str(item.get("scope_type", "")) == "user" else 1 if str(item.get("scope_type", "")) == "repo" else 2,
                str(item.get("strategy_key", "")),
                -float(item.get("weight", 0.0)),
            )
        )
        return matched[:max(limit, 1)]

    def decode_feature_json(self, value: Any) -> dict[str, float]:
        raw = self._json_loads(value, fallback={})
        if not isinstance(raw, dict):
            return {}
        decoded: dict[str, float] = {}
        for key, raw_value in raw.items():
            try:
                decoded[str(key)] = float(raw_value)
            except (TypeError, ValueError):
                continue
        return decoded


    def upsert_embedding(
        self,
        *,
        object_type: str,
        object_id: int,
        embedding_model: str,
        vector_dim: int,
        vector_blob: bytes,
        norm: float,
    ) -> None:
        with self.managed_connection() as conn:
            conn.execute(
                """
                INSERT INTO embeddings(object_type, object_id, embedding_model, vector_dim, vector_blob, norm, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(object_type, object_id, embedding_model) DO UPDATE SET
                    vector_dim = excluded.vector_dim,
                    vector_blob = excluded.vector_blob,
                    norm = excluded.norm,
                    updated_at = excluded.updated_at
                """,
                (
                    object_type,
                    int(object_id),
                    embedding_model,
                    int(vector_dim),
                    sqlite3.Binary(vector_blob),
                    float(norm),
                    utc_now_iso(),
                    utc_now_iso(),
                ),
            )

    def load_embeddings(
        self,
        *,
        object_type: str,
        object_ids: list[int],
        embedding_model: str,
    ) -> dict[int, dict[str, Any]]:
        if not object_ids:
            return {}
        placeholders = ", ".join(["?"] * len(object_ids))
        with self.managed_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT object_id, vector_dim, vector_blob, norm, updated_at
                FROM embeddings
                WHERE object_type = ? AND embedding_model = ? AND object_id IN ({placeholders})
                """,
                [object_type, embedding_model, *object_ids],
            ).fetchall()
        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            result[int(row["object_id"])] = {
                "vector_dim": int(row["vector_dim"]),
                "vector_blob": bytes(row["vector_blob"]),
                "norm": float(row["norm"]),
                "updated_at": str(row["updated_at"]),
            }
        return result

    def get_pattern_embedding_source(self, pattern_id: int) -> dict[str, Any] | None:
        with self.managed_connection() as conn:
            row = conn.execute(
                """
                SELECT id, title, project_scope, domain, error_family, root_cause_class,
                       canonical_symptom, canonical_fix, prevention_rule, verification_steps,
                       tags, signature, times_seen, confidence, created_at, updated_at
                FROM issue_patterns
                WHERE id = ?
                """,
                (pattern_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def get_variant_embedding_source(self, variant_id: int) -> dict[str, Any] | None:
        with self.managed_connection() as conn:
            row = conn.execute(
                """
                SELECT
                    p.id AS pattern_id,
                    p.title AS pattern_title,
                    p.project_scope,
                    p.domain,
                    p.error_family,
                    p.root_cause_class,
                    p.canonical_symptom,
                    p.canonical_fix AS pattern_canonical_fix,
                    p.prevention_rule,
                    p.verification_steps AS pattern_verification_steps,
                    p.tags AS pattern_tags,
                    p.signature,
                    p.times_seen,
                    p.confidence AS pattern_confidence,
                    p.created_at AS pattern_created_at,
                    p.updated_at AS pattern_updated_at,
                    v.id AS variant_id,
                    v.variant_key,
                    v.title,
                    v.repo_fingerprint,
                    v.env_fingerprint,
                    v.command_signature,
                    v.file_path_signature,
                    v.stack_signature,
                    v.patch_summary,
                    v.canonical_fix,
                    v.verification_steps,
                    v.rollback_steps,
                    v.tags_json AS variant_tags_json,
                    v.times_used,
                    v.success_count,
                    v.reject_count,
                    v.confidence,
                    v.memory_strength,
                    v.updated_at AS variant_updated_at
                FROM issue_variants v
                JOIN issue_patterns p ON p.id = v.pattern_id
                WHERE v.id = ?
                """,
                (variant_id,),
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["variant_tags_json"] = self._json_loads(data.get("variant_tags_json"), fallback=[])
            return data

    def get_dense_pattern_sources(
        self,
        *,
        project_scope: str,
        error_family: str,
        root_cause_class: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self.managed_connection() as conn:
            scope_predicate, scope_predicate_args, scope_order, scope_order_args = self._scope_predicate(project_scope)
            if error_family and error_family != "generic_runtime_error":
                family_order = "CASE WHEN p.root_cause_class = ? THEN 0 WHEN p.error_family = ? THEN 1 ELSE 2 END"
                family_args: list[Any] = [root_cause_class, error_family]
            else:
                family_order = "CASE WHEN 1=1 THEN 0 ELSE 0 END"
                family_args = []
            rows = conn.execute(
                f"""
                SELECT p.id AS id, p.title, p.project_scope, p.domain, p.error_family, p.root_cause_class,
                       p.canonical_symptom, p.canonical_fix, p.prevention_rule, p.verification_steps,
                       p.tags, p.signature, p.times_seen, p.confidence, p.created_at, p.updated_at
                FROM issue_patterns p
                WHERE {scope_predicate}
                ORDER BY {family_order}, {scope_order}, p.confidence DESC, p.updated_at DESC, p.id ASC
                LIMIT ?
                """,
                [*scope_predicate_args, *family_args, *scope_order_args, int(limit)],
            ).fetchall()
            return [dict(row) for row in rows]

    def get_dense_variant_sources(
        self,
        *,
        project_scope: str,
        error_family: str,
        root_cause_class: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self.managed_connection() as conn:
            scope_predicate, scope_predicate_args, scope_order, scope_order_args = self._scope_predicate(project_scope)
            if error_family and error_family != "generic_runtime_error":
                family_order = "CASE WHEN p.root_cause_class = ? THEN 0 WHEN p.error_family = ? THEN 1 ELSE 2 END"
                family_args = [root_cause_class, error_family]
            else:
                family_order = "CASE WHEN 1=1 THEN 0 ELSE 0 END"
                family_args = []
            rows = conn.execute(
                f"""
                SELECT
                    p.id AS pattern_id,
                    p.title AS pattern_title,
                    p.project_scope,
                    p.domain,
                    p.error_family,
                    p.root_cause_class,
                    p.canonical_symptom,
                    p.canonical_fix AS pattern_canonical_fix,
                    p.prevention_rule,
                    p.verification_steps AS pattern_verification_steps,
                    p.tags AS pattern_tags,
                    p.signature,
                    p.times_seen,
                    p.confidence AS pattern_confidence,
                    p.created_at AS pattern_created_at,
                    p.updated_at AS pattern_updated_at,
                    v.id AS variant_id,
                    v.variant_key,
                    v.title,
                    v.repo_fingerprint,
                    v.env_fingerprint,
                    v.command_signature,
                    v.file_path_signature,
                    v.stack_signature,
                    v.patch_summary,
                    v.canonical_fix,
                    v.verification_steps,
                    v.rollback_steps,
                    v.tags_json AS variant_tags_json,
                    v.times_used,
                    v.success_count,
                    v.reject_count,
                    v.confidence,
                    v.memory_strength,
                    v.updated_at AS variant_updated_at
                FROM issue_variants v
                JOIN issue_patterns p ON p.id = v.pattern_id
                WHERE v.status = 'active' AND {scope_predicate}
                ORDER BY {family_order}, {scope_order}, v.confidence DESC, v.updated_at DESC, v.id ASC
                LIMIT ?
                """,
                [*scope_predicate_args, *family_args, *scope_order_args, int(limit)],
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["variant_tags_json"] = self._json_loads(data.get("variant_tags_json"), fallback=[])
            results.append(data)
        return results

    @staticmethod
    def _session_expiry_iso(ttl_seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=max(int(ttl_seconds), 60))).replace(microsecond=0).isoformat()

    def _purge_expired_session_memory_tx(self, conn: sqlite3.Connection) -> int:
        now = utc_now_iso()
        count_row = conn.execute(
            "SELECT COUNT(*) AS count FROM session_memory WHERE expires_at <= ?",
            (now,),
        ).fetchone()
        conn.execute("DELETE FROM session_memory WHERE expires_at <= ?", (now,))
        return int(count_row["count"]) if count_row is not None else 0

    def upsert_session_memory(
        self,
        *,
        session_id: str,
        project_scope: str,
        repo_name: str,
        memory_key: str,
        memory_value: dict[str, Any],
        salience: float,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        with self.managed_connection() as conn:
            self._purge_expired_session_memory_tx(conn)
            row = conn.execute(
                "SELECT * FROM session_memory WHERE session_id = ? AND memory_key = ?",
                (session_id, memory_key),
            ).fetchone()
            now = utc_now_iso()
            expires_at = self._session_expiry_iso(ttl_seconds)
            if row is None:
                cur = conn.execute(
                    """
                    INSERT INTO session_memory(
                        session_id, project_scope, repo_name, memory_key, memory_value_json,
                        salience, ttl_seconds, expires_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        project_scope or "global",
                        repo_name,
                        memory_key,
                        self._json_dumps(memory_value),
                        min(max(float(salience), 0.0), 1.0),
                        int(ttl_seconds),
                        expires_at,
                        now,
                        now,
                    ),
                )
                if cur.lastrowid is None:
                    raise RuntimeError("Failed to determine session memory id after insert")
                row_id = int(cur.lastrowid)
            else:
                existing_value = self._json_loads(row["memory_value_json"], fallback={})
                merged_value = {}
                if isinstance(existing_value, dict):
                    merged_value.update(existing_value)
                merged_value.update(memory_value)
                merged_salience = min(max(max(float(row["salience"]), float(salience)), 0.0), 1.0)
                conn.execute(
                    """
                    UPDATE session_memory
                    SET project_scope = ?, repo_name = ?, memory_value_json = ?, salience = ?,
                        ttl_seconds = ?, expires_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        project_scope or row["project_scope"],
                        repo_name or row["repo_name"],
                        self._json_dumps(merged_value),
                        merged_salience,
                        int(ttl_seconds),
                        expires_at,
                        now,
                        int(row["id"]),
                    ),
                )
                row_id = int(row["id"])
            stored = conn.execute("SELECT * FROM session_memory WHERE id = ?", (row_id,)).fetchone()
            return self._decode_session_memory_row(stored)

    def clear_session_memory_key(self, *, session_id: str, memory_key: str) -> None:
        with self.managed_connection() as conn:
            conn.execute(
                "DELETE FROM session_memory WHERE session_id = ? AND memory_key = ?",
                (session_id, memory_key),
            )

    def _decode_session_memory_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["memory_value_json"] = self._json_loads(data.get("memory_value_json"), fallback={})
        return data

    def get_session_memory(self, session_id: str) -> list[dict[str, Any]]:
        with self.managed_connection() as conn:
            self._purge_expired_session_memory_tx(conn)
            rows = conn.execute(
                """
                SELECT * FROM session_memory
                WHERE session_id = ?
                ORDER BY salience DESC, updated_at DESC, id ASC
                """,
                (session_id,),
            ).fetchall()
            return [self._decode_session_memory_row(row) for row in rows]

    def apply_session_penalties(
        self,
        candidates: list[dict[str, Any]],
        *,
        session_id: str,
        project_scope: str = "global",
        repo_name: str = "",
    ) -> None:
        if not session_id or not candidates:
            return
        snapshot = self.get_session_memory(session_id)
        rejections: list[tuple[int, int | None, float]] = []
        acceptances: list[tuple[int, int | None, float]] = []
        current_scope = project_scope or "global"
        current_repo = repo_name.strip().lower()

        for row in snapshot:
            row_scope = str(row.get("project_scope", "global") or "global")
            row_repo = str(row.get("repo_name", "") or "").strip().lower()
            scope_matches = row_scope == "global" or row_scope == current_scope
            repo_matches = not row_repo or not current_repo or row_repo == current_repo
            if not (scope_matches and repo_matches):
                continue

            key = str(row.get("memory_key", ""))
            value = row.get("memory_value_json") or {}
            if not isinstance(value, dict):
                value = {}
            raw_variant_id = value.get("variant_id")
            variant_id = int(raw_variant_id) if raw_variant_id not in (None, "") else None
            salience = min(max(float(row.get("salience", 0.0)), 0.0), 1.0)

            parts = key.split(":")
            if len(parts) < 2:
                continue
            try:
                pattern_id = int(parts[1])
            except ValueError:
                continue
            if parts[0] == "rejected_variant":
                if len(parts) >= 3:
                    try:
                        variant_id = int(parts[2])
                    except ValueError:
                        variant_id = variant_id
                rejections.append((pattern_id, variant_id, salience))
            elif parts[0] == "rejected_pattern":
                rejections.append((pattern_id, None, salience))
            elif parts[0] == "accepted_variant":
                if len(parts) >= 3:
                    try:
                        variant_id = int(parts[2])
                    except ValueError:
                        variant_id = variant_id
                acceptances.append((pattern_id, variant_id, salience))
            elif parts[0] == "accepted_pattern":
                acceptances.append((pattern_id, None, salience))

        for candidate in candidates:
            pattern_id = int(candidate.get("pattern_id", candidate["id"]))
            raw_variant_id = candidate.get("variant_id")
            variant_id = int(raw_variant_id) if raw_variant_id not in (None, "") else None
            penalty = 0.0
            boost = 0.0
            for rejected_pattern_id, rejected_variant_id, salience in rejections:
                if rejected_pattern_id != pattern_id:
                    continue
                if rejected_variant_id is not None and variant_id is not None and rejected_variant_id == variant_id:
                    penalty = max(penalty, salience)
                elif rejected_variant_id is not None:
                    penalty = max(penalty, salience * 0.20)
                else:
                    penalty = max(penalty, salience * 0.60)
            for accepted_pattern_id, accepted_variant_id, salience in acceptances:
                if accepted_pattern_id != pattern_id:
                    continue
                if accepted_variant_id is not None and variant_id is not None and accepted_variant_id == variant_id:
                    boost = max(boost, salience * 0.32)
                elif accepted_variant_id is not None:
                    boost = max(boost, salience * 0.08)
                else:
                    boost = max(boost, salience * 0.16)
            candidate["session_penalty"] = round(min(max(penalty, 0.0), 1.0), 6)
            candidate["session_boost"] = round(min(max(boost, 0.0), 1.0), 6)

    def _variant_context_score(
        self,
        variant: dict[str, Any],
        *,
        repo_fingerprint: str,
        env_fingerprint: str,
        command_signature: str,
        path_signature: str,
        stack_signature: str,
    ) -> float:
        score = 0.0
        if repo_fingerprint and variant.get("repo_fingerprint") == repo_fingerprint:
            score += 0.12
        if env_fingerprint and variant.get("env_fingerprint") == env_fingerprint:
            score += 0.14
        if command_signature and variant.get("command_signature") == command_signature:
            score += 0.22
        if path_signature and variant.get("file_path_signature") == path_signature:
            score += 0.18
        if stack_signature and variant.get("stack_signature") == stack_signature:
            score += 0.28
        score += min(int(variant.get("times_used", 0)), 5) * 0.02
        score += min(max(float(variant.get("confidence", 0.0)), 0.0), 1.0) * 0.08
        return score

    def enrich_candidates_with_variants(
        self,
        candidates: list[dict[str, Any]],
        *,
        repo_fingerprint: str,
        env_fingerprint: str,
        command_signature: str,
        path_signature: str,
        stack_signature: str,
    ) -> None:
        if not candidates:
            return
        pattern_ids = [int(candidate["id"]) for candidate in candidates]
        placeholders = ", ".join(["?"] * len(pattern_ids))
        with self.managed_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM issue_variants
                WHERE pattern_id IN ({placeholders}) AND status = 'active'
                ORDER BY pattern_id ASC, updated_at DESC, id ASC
                """,
                pattern_ids,
            ).fetchall()
        by_pattern: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            variant = self._decode_variant_row(row)
            by_pattern[int(variant["pattern_id"])].append(variant)
        for candidate in candidates:
            best_variant: dict[str, Any] | None = None
            best_score = -1.0
            for variant in by_pattern.get(int(candidate["id"]), []):
                variant_score = self._variant_context_score(
                    variant,
                    repo_fingerprint=repo_fingerprint,
                    env_fingerprint=env_fingerprint,
                    command_signature=command_signature,
                    path_signature=path_signature,
                    stack_signature=stack_signature,
                )
                if variant_score > best_score:
                    best_score = variant_score
                    best_variant = variant
            if best_variant is not None:
                candidate["best_variant"] = best_variant
                candidate["variant_match_score"] = round(best_score, 6)
            else:
                candidate["best_variant"] = None
                candidate["variant_match_score"] = 0.0

    def log_retrieval_event(
        self,
        *,
        profile: Any,
        ranked: list[Any],
        decision: Any,
        project_scope: str,
        session_id: str,
        repo_name: str,
        retrieval_mode: str,
        latency_ms: int,
    ) -> dict[str, Any]:
        with self.managed_connection() as conn:
            request_uuid = uuid4().hex
            now = utc_now_iso()
            top = ranked[0] if ranked and decision.status != "abstain" else None
            top_candidate = top.candidate if top is not None else None
            top_variant = (top_candidate.get("best_variant") or {}) if top_candidate is not None else {}
            selected_pattern_id = int(top_candidate.get("pattern_id", top_candidate["id"])) if top_candidate is not None else None
            selected_variant_id = (
                int(top_candidate.get("variant_id"))
                if top_candidate is not None and top_candidate.get("variant_id") not in (None, "")
                else (int(top_variant["id"]) if top_variant else None)
            )
            cur = conn.execute(
                """
                INSERT INTO retrieval_events(
                    request_uuid, session_id, project_scope, repo_name, repo_fingerprint,
                    raw_query, normalized_query, command, file_path, error_family,
                    root_cause_class, exception_types_json, entity_slots_json, strategy_hints_json,
                    user_scope, env_fingerprint, retrieval_mode, decision_status, decision_confidence,
                    abstain_reason, selected_pattern_id, selected_variant_id, selected_candidate_rank,
                    latency_ms, token_cost_estimate, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_uuid,
                    session_id,
                    project_scope or "global",
                    repo_name,
                    getattr(profile, "repo_fingerprint", ""),
                    getattr(profile, "raw_text", ""),
                    getattr(profile, "normalized_text", ""),
                    " ".join(getattr(profile, "command_tokens", [])),
                    " ".join(getattr(profile, "path_tokens", [])),
                    getattr(profile, "error_family", "generic_runtime_error"),
                    getattr(profile, "root_cause_class", "unknown"),
                    self._json_dumps(list(getattr(profile, "exception_types", []))),
                    self._json_dumps(dict(getattr(profile, "entity_slots", {}))),
                    self._json_dumps(list(getattr(profile, "strategy_hints", []))),
                    getattr(profile, "user_scope", ""),
                    getattr(profile, "env_fingerprint", ""),
                    retrieval_mode,
                    getattr(decision, "status", "abstain"),
                    float(getattr(decision, "confidence", 0.0)),
                    str(getattr(decision, "reason", "")),
                    selected_pattern_id,
                    selected_variant_id,
                    1 if top_candidate is not None else None,
                    int(latency_ms),
                    0,
                    now,
                ),
            )
            if cur.lastrowid is None:
                raise RuntimeError("Failed to determine retrieval event id after insert")
            event_id = int(cur.lastrowid)
            ids_by_rank: dict[int, int] = {}
            for rank, item in enumerate(ranked[:12], start=1):
                candidate = item.candidate
                variant = candidate.get("best_variant") or {}
                pattern_id = int(candidate.get("pattern_id", candidate["id"]))
                raw_variant_id = candidate.get("variant_id")
                variant_id = int(raw_variant_id) if raw_variant_id not in (None, "") else (int(variant["id"]) if variant else None)
                candidate_type = str(candidate.get("candidate_type", "variant" if variant else "pattern"))
                feature_json = self._json_dumps(item.features)
                reason_json = self._json_dumps(item.reasons)
                cur = conn.execute(
                    """
                    INSERT INTO retrieval_candidates(
                        retrieval_event_id, candidate_rank, candidate_type, pattern_id, variant_id,
                        total_score, scope_score, family_score, root_score, sparse_score,
                        dense_score, text_overlap_score, example_score, env_score,
                        success_prior_score, recency_score, session_penalty_score,
                        feature_json, reason_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        rank,
                        candidate_type,
                        pattern_id,
                        variant_id,
                        float(item.score),
                        float(item.features.get("scope_score", 0.0)),
                        float(item.features.get("family_score", 0.0)),
                        float(item.features.get("root_score", 0.0)),
                        float(item.features.get("lexical_score", 0.0)),
                        float(item.features.get("dense_score", 0.0)),
                        float(item.features.get("text_overlap_score", 0.0)),
                        float(item.features.get("example_score", 0.0)),
                        float(item.features.get("env_score", 0.0)),
                        float(item.features.get("success_prior_score", 0.0)),
                        float(item.features.get("recency_score", 0.0)),
                        float(item.features.get("session_penalty_score", 0.0)),
                        feature_json,
                        reason_json,
                        now,
                    ),
                )
                if cur.lastrowid is None:
                    raise RuntimeError("Failed to determine retrieval candidate id after insert")
                ids_by_rank[rank] = int(cur.lastrowid)
            return {
                "event_id": event_id,
                "request_uuid": request_uuid,
                "candidate_ids_by_rank": ids_by_rank,
            }

    def get_retrieval_event(self, retrieval_event_id: int) -> dict[str, Any] | None:
        with self.managed_connection() as conn:
            row = conn.execute(
                "SELECT * FROM retrieval_events WHERE id = ?",
                (retrieval_event_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def resolve_retrieval_candidate(
        self,
        *,
        retrieval_event_id: int,
        retrieval_candidate_id: int | None = None,
        candidate_rank: int | None = None,
        pattern_id: int | None = None,
        variant_id: int | None = None,
    ) -> dict[str, Any] | None:
        with self.managed_connection() as conn:
            row: sqlite3.Row | None = None
            if retrieval_candidate_id is not None:
                row = conn.execute(
                    "SELECT * FROM retrieval_candidates WHERE retrieval_event_id = ? AND id = ?",
                    (retrieval_event_id, retrieval_candidate_id),
                ).fetchone()
            elif candidate_rank is not None:
                row = conn.execute(
                    "SELECT * FROM retrieval_candidates WHERE retrieval_event_id = ? AND candidate_rank = ?",
                    (retrieval_event_id, candidate_rank),
                ).fetchone()
            elif pattern_id is not None or variant_id is not None:
                clauses = ["retrieval_event_id = ?"]
                args: list[Any] = [retrieval_event_id]
                if pattern_id is not None:
                    clauses.append("pattern_id = ?")
                    args.append(pattern_id)
                if variant_id is not None:
                    clauses.append("variant_id = ?")
                    args.append(variant_id)
                row = conn.execute(
                    f"SELECT * FROM retrieval_candidates WHERE {' AND '.join(clauses)} ORDER BY candidate_rank ASC LIMIT 1",
                    args,
                ).fetchone()
            else:
                event = conn.execute(
                    "SELECT selected_candidate_rank FROM retrieval_events WHERE id = ?",
                    (retrieval_event_id,),
                ).fetchone()
                rank = int(event["selected_candidate_rank"]) if event and event["selected_candidate_rank"] is not None else 1
                row = conn.execute(
                    "SELECT * FROM retrieval_candidates WHERE retrieval_event_id = ? AND candidate_rank = ?",
                    (retrieval_event_id, rank),
                ).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["feature_json"] = self.decode_feature_json(data.get("feature_json"))
            data["reason_json"] = self._json_loads(data.get("reason_json"), fallback=[])
            return data

    @staticmethod
    def _normalized_scope_key(scope_type: str, scope_key: str) -> str:
        return '' if scope_type == 'global' else scope_key.strip()

    @staticmethod
    def _decay_beta_parameters(
        *,
        alpha: float,
        beta: float,
        updated_at: str,
        half_life_days: int,
        prior_alpha: float,
        prior_beta: float,
    ) -> tuple[float, float, float]:
        if half_life_days <= 0:
            return max(alpha, prior_alpha), max(beta, prior_beta), 1.0
        updated_dt = parse_iso_datetime(updated_at)
        if updated_dt is None:
            return max(alpha, prior_alpha), max(beta, prior_beta), 1.0
        age_days = max((datetime.now(timezone.utc) - updated_dt).total_seconds() / 86400.0, 0.0)
        if age_days <= 0.0:
            return max(alpha, prior_alpha), max(beta, prior_beta), 1.0
        decay = 0.5 ** (age_days / float(half_life_days))
        decayed_alpha = prior_alpha + max(alpha - prior_alpha, 0.0) * decay
        decayed_beta = prior_beta + max(beta - prior_beta, 0.0) * decay
        return decayed_alpha, decayed_beta, decay

    @staticmethod
    def _strategy_scope_entries(*, repo_name: str, user_scope: str) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = [('global', '')]
        normalized_repo_name = repo_name.strip()
        normalized_user_scope = user_scope.strip()
        if normalized_repo_name:
            entries.append(('repo', normalized_repo_name))
        if normalized_user_scope:
            entries.append(('user', normalized_user_scope))
        return entries

    def _merge_negative_applicability(
        self,
        existing: dict[str, Any],
        *,
        event: sqlite3.Row | None,
        notes: str,
        now: str,
    ) -> dict[str, Any]:
        payload = dict(existing) if isinstance(existing, dict) else {}
        def _append_unique(key: str, value: str, *, limit: int = 8) -> None:
            normalized = value.strip()
            if not normalized:
                return
            current = payload.get(key, [])
            if not isinstance(current, list):
                current = []
            merged = [str(item) for item in current if str(item).strip() and str(item).strip() != normalized]
            merged.append(normalized)
            payload[key] = merged[-limit:]

        payload['false_positive_count'] = int(payload.get('false_positive_count', 0)) + 1
        payload['last_false_positive_at'] = now
        if event is not None:
            _append_unique('project_scopes', str(event['project_scope']))
            _append_unique('user_scopes', str(event['user_scope']))
            _append_unique('repo_names', str(event['repo_name']))
            _append_unique('commands', str(event['command']))
            _append_unique('file_paths', str(event['file_path']))
        if notes.strip():
            current_notes = payload.get('notes', [])
            if not isinstance(current_notes, list):
                current_notes = []
            normalized_notes = [str(item) for item in current_notes if str(item).strip()]
            normalized_notes.append(notes.strip())
            payload['notes'] = normalized_notes[-5:]
        return payload

    def _update_strategy_stat_tx(
        self,
        conn: sqlite3.Connection,
        *,
        scope_type: str,
        scope_key: str,
        strategy_key: str,
        success: bool,
        now: str,
    ) -> dict[str, Any] | None:
        normalized_key = self._normalized_scope_key(scope_type, scope_key)
        if not strategy_key.strip():
            return None
        row = conn.execute(
            """
            SELECT * FROM strategy_stats
            WHERE scope_type = ? AND scope_key = ? AND strategy_key = ?
            """,
            (scope_type, normalized_key, strategy_key),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO strategy_stats(
                    scope_type, scope_key, strategy_key, alpha, beta,
                    success_count, failure_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope_type,
                    normalized_key,
                    strategy_key,
                    STRATEGY_PRIOR_ALPHA,
                    STRATEGY_PRIOR_BETA,
                    0,
                    0,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM strategy_stats
                WHERE scope_type = ? AND scope_key = ? AND strategy_key = ?
                """,
                (scope_type, normalized_key, strategy_key),
            ).fetchone()
        assert row is not None
        alpha_before = float(row['alpha'])
        beta_before = float(row['beta'])
        decayed_alpha, decayed_beta, decay = self._decay_beta_parameters(
            alpha=alpha_before,
            beta=beta_before,
            updated_at=str(row['updated_at']),
            half_life_days=self.settings.strategy_half_life_days,
            prior_alpha=STRATEGY_PRIOR_ALPHA,
            prior_beta=STRATEGY_PRIOR_BETA,
        )
        success_delta = 1 if success else 0
        failure_delta = 0 if success else 1
        alpha_after = decayed_alpha + success_delta
        beta_after = decayed_beta + failure_delta
        success_count = int(row['success_count']) + success_delta
        failure_count = int(row['failure_count']) + failure_delta
        conn.execute(
            """
            UPDATE strategy_stats
            SET alpha = ?, beta = ?, success_count = ?, failure_count = ?, updated_at = ?
            WHERE scope_type = ? AND scope_key = ? AND strategy_key = ?
            """,
            (alpha_after, beta_after, success_count, failure_count, now, scope_type, normalized_key, strategy_key),
        )
        updated_row = conn.execute(
            """
            SELECT * FROM strategy_stats
            WHERE scope_type = ? AND scope_key = ? AND strategy_key = ?
            """,
            (scope_type, normalized_key, strategy_key),
        ).fetchone()
        assert updated_row is not None
        data = self._decode_strategy_stat_row(updated_row)
        data['alpha_before'] = alpha_before
        data['beta_before'] = beta_before
        data['decay_factor'] = round(decay, 6)
        return data

    def _update_variant_stat_tx(
        self,
        conn: sqlite3.Connection,
        *,
        variant_id: int,
        success: bool,
        now: str,
        weight: float = 1.0,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM variant_stats WHERE variant_id = ?",
            (variant_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO variant_stats(variant_id, alpha, beta, success_count, failure_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (variant_id, VARIANT_PRIOR_ALPHA, VARIANT_PRIOR_BETA, 0, 0, now),
            )
            row = conn.execute(
                "SELECT * FROM variant_stats WHERE variant_id = ?",
                (variant_id,),
            ).fetchone()
        assert row is not None
        alpha_before = float(row['alpha'])
        beta_before = float(row['beta'])
        decayed_alpha, decayed_beta, decay = self._decay_beta_parameters(
            alpha=alpha_before,
            beta=beta_before,
            updated_at=str(row['updated_at']),
            half_life_days=self.settings.variant_half_life_days,
            prior_alpha=VARIANT_PRIOR_ALPHA,
            prior_beta=VARIANT_PRIOR_BETA,
        )
        success_delta = weight if success else 0.0
        failure_delta = 0.0 if success else weight
        alpha_after = decayed_alpha + success_delta
        beta_after = decayed_beta + failure_delta
        success_count = int(row['success_count']) + (1 if success else 0)
        failure_count = int(row['failure_count']) + (0 if success else 1)
        conn.execute(
            """
            UPDATE variant_stats
            SET alpha = ?, beta = ?, success_count = ?, failure_count = ?, updated_at = ?
            WHERE variant_id = ?
            """,
            (alpha_after, beta_after, success_count, failure_count, now, variant_id),
        )
        updated_row = conn.execute(
            "SELECT * FROM variant_stats WHERE variant_id = ?",
            (variant_id,),
        ).fetchone()
        assert updated_row is not None
        data = self._decode_variant_stat_row(updated_row)
        data['alpha_before'] = alpha_before
        data['beta_before'] = beta_before
        data['decay_factor'] = round(decay, 6)
        return data

    def _apply_strong_feedback_tx(
        self,
        conn: sqlite3.Connection,
        *,
        retrieval_event_id: int,
        pattern_id: int | None,
        variant_id: int | None,
        feedback_type: str,
        reward: float,
        notes: str,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        success = reward > 0
        result: dict[str, Any] = {
            'global_update_applied': False,
            'strategy_stat_updates': [],
            'variant_stat_update': None,
            'negative_applicability_applied': False,
        }
        event = conn.execute(
            'SELECT * FROM retrieval_events WHERE id = ?',
            (retrieval_event_id,),
        ).fetchone()
        if feedback_type not in STRONG_GLOBAL_FEEDBACK:
            if feedback_type in WEAK_FEEDBACK_WEIGHTS and variant_id is not None:
                weak_weight = WEAK_FEEDBACK_WEIGHTS[feedback_type]
                weak_success = feedback_type in WEAK_POSITIVE_FEEDBACK
                result['variant_stat_update'] = self._update_variant_stat_tx(
                    conn,
                    variant_id=variant_id,
                    success=weak_success,
                    now=now,
                    weight=weak_weight,
                )
            return result
        result['global_update_applied'] = True
        if pattern_id is not None:
            row = conn.execute('SELECT * FROM issue_patterns WHERE id = ?', (pattern_id,)).fetchone()
            if row is not None:
                confidence_before = float(row['confidence'])
                confidence_after = self._clamp(confidence_before + (0.12 if success else -0.12))
                conn.execute(
                    'UPDATE issue_patterns SET confidence = ?, updated_at = ? WHERE id = ?',
                    (confidence_after, now, pattern_id),
                )
                result['pattern'] = {
                    'pattern_id': pattern_id,
                    'confidence_before': round(confidence_before, 4),
                    'confidence_after': round(confidence_after, 4),
                }
        variant_row = None
        if variant_id is not None:
            variant_row = conn.execute('SELECT * FROM issue_variants WHERE id = ?', (variant_id,)).fetchone()
            if variant_row is not None:
                success_delta = 1 if success else 0
                reject_delta = 0 if success else 1
                confidence_before = float(variant_row['confidence'])
                memory_before = float(variant_row['memory_strength'])
                confidence_after = self._clamp(confidence_before + (0.16 if success else -0.16))
                memory_after = self._clamp(memory_before + (0.12 if success else -0.10))
                negative_applicability = self._json_loads(variant_row['negative_applicability_json'], fallback={})
                if not success:
                    negative_applicability = self._merge_negative_applicability(
                        negative_applicability,
                        event=event,
                        notes=notes,
                        now=now,
                    )
                    result['negative_applicability_applied'] = True
                conn.execute(
                    """
                    UPDATE issue_variants
                    SET success_count = ?, reject_count = ?, confidence = ?, memory_strength = ?,
                        negative_applicability_json = ?, last_used_at = ?, last_verified_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        int(variant_row['success_count']) + success_delta,
                        int(variant_row['reject_count']) + reject_delta,
                        confidence_after,
                        memory_after,
                        self._json_dumps(negative_applicability),
                        now,
                        now if success else variant_row['last_verified_at'],
                        now,
                        variant_id,
                    ),
                )
                result['variant'] = {
                    'variant_id': variant_id,
                    'confidence_before': round(confidence_before, 4),
                    'confidence_after': round(confidence_after, 4),
                    'memory_strength_before': round(memory_before, 4),
                    'memory_strength_after': round(memory_after, 4),
                }
                strategy_key = str(variant_row['strategy_key'])
                result['variant_stat_update'] = self._update_variant_stat_tx(
                    conn,
                    variant_id=variant_id,
                    success=success,
                    now=now,
                )
                if strategy_key:
                    repo_name = str(event['repo_name']) if event is not None else ''
                    user_scope = str(event['user_scope']) if event is not None else ''
                    for scope_type, scope_key in self._strategy_scope_entries(repo_name=repo_name, user_scope=user_scope):
                        stat = self._update_strategy_stat_tx(
                            conn,
                            scope_type=scope_type,
                            scope_key=scope_key,
                            strategy_key=strategy_key,
                            success=success,
                            now=now,
                        )
                        if stat is not None:
                            result['strategy_stat_updates'].append(stat)
        return result

    def _seed_verified_stats_tx(
        self,
        conn: sqlite3.Connection,
        *,
        variant_id: int,
        strategy_key: str,
        repo_name: str,
        user_scope: str,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        strategy_updates: list[dict[str, Any]] = []
        variant_update = self._update_variant_stat_tx(conn, variant_id=variant_id, success=True, now=now)
        if strategy_key.strip():
            for scope_type, scope_key in self._strategy_scope_entries(repo_name=repo_name, user_scope=user_scope):
                stat = self._update_strategy_stat_tx(
                    conn,
                    scope_type=scope_type,
                    scope_key=scope_key,
                    strategy_key=strategy_key,
                    success=True,
                    now=now,
                )
                if stat is not None:
                    strategy_updates.append(stat)
        return {
            'variant_stat_update': variant_update,
            'strategy_stat_updates': strategy_updates,
        }

    def submit_feedback(
        self,
        *,
        retrieval_event_id: int,
        retrieval_candidate_id: int | None,
        pattern_id: int | None,
        variant_id: int | None,
        episode_id: int | None,
        feedback_type: str,
        reward: float,
        actor: str,
        notes: str,
    ) -> dict[str, Any]:
        with self.managed_connection(immediate=True) as conn:
            cur = conn.execute(
                """
                INSERT INTO feedback_events(
                    retrieval_event_id, retrieval_candidate_id, pattern_id, variant_id,
                    episode_id, feedback_type, reward, actor, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    retrieval_event_id,
                    retrieval_candidate_id,
                    pattern_id,
                    variant_id,
                    episode_id,
                    feedback_type,
                    float(reward),
                    actor,
                    notes,
                    utc_now_iso(),
                ),
            )
            if cur.lastrowid is None:
                raise RuntimeError('Failed to determine feedback event id after insert')
            feedback_row = conn.execute(
                'SELECT * FROM feedback_events WHERE id = ?',
                (int(cur.lastrowid),),
            ).fetchone()
            if feedback_row is None:
                raise RuntimeError('Feedback event row could not be loaded after insert')
            strong_updates = self._apply_strong_feedback_tx(
                conn,
                retrieval_event_id=retrieval_event_id,
                pattern_id=pattern_id,
                variant_id=variant_id,
                feedback_type=feedback_type,
                reward=reward,
                notes=notes,
            )
            return {
                'feedback_row': dict(feedback_row),
                'pattern_update': strong_updates.get('pattern'),
                'variant_update': strong_updates.get('variant'),
                'strategy_stat_updates': strong_updates.get('strategy_stat_updates', []),
                'variant_stat_update': strong_updates.get('variant_stat_update'),
                'global_update_applied': bool(strong_updates.get('global_update_applied', False)),
                'negative_applicability_applied': bool(strong_updates.get('negative_applicability_applied', False)),
            }

    def log_feature_outcomes(
        self,
        *,
        feedback_event_id: int,
        retrieval_candidate_id: int,
        features: dict[str, float],
        feedback_type: str,
        reward: float,
        error_family: str,
        project_scope: str,
    ) -> int:
        now = utc_now_iso()
        rows = [
            (feedback_event_id, retrieval_candidate_id, name, float(value),
             feedback_type, float(reward), error_family, project_scope, now)
            for name, value in features.items()
        ]
        if not rows:
            return 0
        with self.managed_connection(immediate=True) as conn:
            conn.executemany(
                """
                INSERT INTO feature_outcome_log(
                    feedback_event_id, retrieval_candidate_id, feature_name, feature_value,
                    feedback_type, reward, error_family, project_scope, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def query_feature_outcome_stats(self) -> list[dict[str, Any]]:
        with self.managed_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    feature_name,
                    COUNT(*) AS sample_count,
                    AVG(feature_value) AS avg_value,
                    AVG(reward) AS avg_reward,
                    AVG(CASE WHEN reward > 0 THEN feature_value END) AS avg_positive,
                    AVG(CASE WHEN reward < 0 THEN feature_value END) AS avg_negative,
                    SUM(CASE WHEN reward > 0 THEN 1 ELSE 0 END) AS positive_count,
                    SUM(CASE WHEN reward < 0 THEN 1 ELSE 0 END) AS negative_count
                FROM feature_outcome_log
                GROUP BY feature_name
                ORDER BY sample_count DESC
                """,
            ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
        return min(max(float(value), low), high)
