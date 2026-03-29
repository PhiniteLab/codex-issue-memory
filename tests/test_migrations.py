from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.storage import IssueMemoryStore


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-migrate-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        self.store = IssueMemoryStore.from_env()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initialize_applies_v2_foundation(self) -> None:
        self.store.initialize()
        schema = self.store.schema_state()
        self.assertEqual(schema.current_version, 9)
        self.assertEqual(schema.applied_count, 8)

        with self.store.managed_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                ).fetchall()
            }

        self.assertIn("issue_patterns", tables)
        self.assertIn("issue_examples", tables)
        self.assertIn("issue_variants", tables)
        self.assertIn("issue_episodes", tables)
        self.assertIn("retrieval_events", tables)
        self.assertIn("feedback_events", tables)
        self.assertIn("session_memory", tables)
        self.assertIn("app_metadata", tables)
        self.assertNotIn("contextual_bandit_state", tables)
        self.assertNotIn("ranker_state", tables)
        self.assertIn("strategy_stats", tables)
        self.assertIn("variant_stats", tables)
        self.assertIn("preference_rules", tables)
        self.assertIn("review_queue", tables)

    def test_v1_to_v2_migration_preserves_existing_records(self) -> None:
        self.store.migrate(target_version=1)
        self.assertEqual(self.store.schema_state().current_version, 1)

        pattern = self.store.create_pattern(
            {
                "title": "Legacy sqlite cwd issue",
                "project_scope": "global",
                "domain": "python",
                "error_family": "sqlite_error",
                "root_cause_class": "cwd_relative_path_bug",
                "canonical_symptom": "sqlite file not found outside repo root",
                "canonical_fix": "Resolve the path relative to __file__.",
                "prevention_rule": "Do not depend on cwd for DB paths.",
                "verification_steps": "Run from repo root and a foreign cwd.",
                "tags": ["sqlite", "cwd"],
                "signature": "global|sqlite_error|cwd_relative_path_bug|sqlite-path",
            }
        )
        example = self.store.add_example(
            pattern_id=int(pattern["id"]),
            raw_error="FileNotFoundError: contractsDatabase.sqlite3",
            normalized_error="filenotfounderror path_contractsdatabase.sqlite3",
            command="python -m app.main",
            file_path="services/db_loader.py",
            verified_fix="Resolve the path relative to __file__.",
        )
        self.assertGreater(int(example["id"]), 0)

        upgraded = self.store.migrate()
        self.assertEqual(upgraded.current_version, 9)

        bundle = self.store.get_pattern(int(pattern["id"]), include_examples=True)
        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(bundle.pattern["title"], "Legacy sqlite cwd issue")
        self.assertEqual(len(bundle.examples), 1)

        with self.store.managed_connection() as conn:
            app_meta = conn.execute(
                "SELECT value FROM app_metadata WHERE key = 'schema_generation'"
            ).fetchone()
            self.assertIsNotNone(app_meta)
            self.assertEqual(app_meta["value"], "v2-foundation")

    def test_cleanup_migration_drops_retired_learning_state_on_upgrade(self) -> None:
        self.store.migrate(target_version=8)
        self.assertEqual(self.store.schema_state().current_version, 8)

        with self.store.managed_connection() as conn:
            conn.execute(
                """
                INSERT INTO ranker_state(model_name, weights_json, bias, learning_rate, fit_count, updated_at)
                VALUES ('default', '{}', 0.0, 0.05, 3, '2026-03-29T00:00:00Z')
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS contextual_bandit_state (
                    model_name TEXT PRIMARY KEY,
                    alpha REAL NOT NULL DEFAULT 0.20,
                    a_diag_json TEXT NOT NULL,
                    b_json TEXT NOT NULL,
                    pull_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO contextual_bandit_state(model_name, alpha, a_diag_json, b_json, pull_count, updated_at)
                VALUES ('variant_selector_linucb', 0.2, '{}', '{}', 4, '2026-03-29T00:00:00Z')
                """
            )

        upgraded = self.store.migrate()
        self.assertEqual(upgraded.current_version, 9)

        with self.store.managed_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                ).fetchall()
            }
            cleanup_meta = conn.execute(
                "SELECT value FROM app_metadata WHERE key = 'learning_state_cleanup'"
            ).fetchone()

        self.assertNotIn("ranker_state", tables)
        self.assertNotIn("contextual_bandit_state", tables)
        self.assertIsNotNone(cleanup_meta)
        assert cleanup_meta is not None
        self.assertEqual(cleanup_meta["value"], "dropped")


if __name__ == "__main__":
    unittest.main()
