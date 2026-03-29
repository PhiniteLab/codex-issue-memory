from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from codex_issue_memory.app import IssueMemoryApp


class VariantSplitMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-variants-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_same_pattern_creates_distinct_variants_without_overwriting_pattern_fix(self) -> None:
        first_fix = "Move optimizer state tensors onto the active CUDA device after checkpoint resume."
        second_fix = "Move each dataloader batch to the model device before the forward pass."

        first = self.app.issue_record_resolution(
            title="PyTorch tensors on mixed devices",
            raw_error="RuntimeError: Expected all tensors to be on the same device, but got cpu and cuda",
            canonical_fix=first_fix,
            prevention_rule="Every training state restore must re-home tensors to the active device.",
            project_scope="global",
            canonical_symptom="mixed cuda and cpu tensors during training",
            verification_steps="Resume from checkpoint and run one optimizer step.",
            tags="pytorch,cuda,cpu,training",
            error_family="tensor_device_error",
            root_cause_class="tensor_cross_device_mix",
            command="python train.py --resume outputs/ckpt.pt",
            file_path="trainer/checkpoint_loader.py",
            stack_excerpt='File "trainer/checkpoint_loader.py", line 42, in restore_optimizer_state',
            env_json='{"python": "3.12", "torch": "2.3.1", "cuda": "12.1"}',
            repo_name="vision-trainer",
            git_commit="abc123def4567890",
            domain="pytorch",
        )

        second = self.app.issue_record_resolution(
            title="PyTorch tensors on mixed devices",
            raw_error="RuntimeError: Expected all tensors to be on the same device, but got cpu and cuda",
            canonical_fix=second_fix,
            prevention_rule="Every minibatch transfer must happen before the forward pass.",
            project_scope="global",
            canonical_symptom="mixed cuda and cpu tensors during training",
            verification_steps="Run one dataloader batch through the model.",
            tags="pytorch,cuda,cpu,training",
            error_family="tensor_device_error",
            root_cause_class="tensor_cross_device_mix",
            command="python train.py",
            file_path="data/dataloader.py",
            stack_excerpt='File "data/dataloader.py", line 18, in move_batch_to_device',
            env_json='{"python": "3.12", "torch": "2.3.1", "cuda": "12.1"}',
            repo_name="vision-trainer",
            git_commit="abc123def4567890",
            domain="pytorch",
        )

        self.assertEqual(first["pattern_id"], second["pattern_id"])
        self.assertNotEqual(first["variant_id"], second["variant_id"])
        self.assertEqual(second["variant_action"], "created")
        self.assertEqual(second["action"], "updated")

        bundle = self.app.issue_get(pattern_id=first["pattern_id"], include_examples=True, examples_limit=10)
        self.assertEqual(bundle["pattern"]["canonical_fix"], first_fix)
        self.assertEqual(len(bundle["variants"]), 2)
        self.assertEqual(len(bundle["episodes"]), 2)
        variant_fixes = {row["canonical_fix"] for row in bundle["variants"]}
        self.assertIn(first_fix, variant_fixes)
        self.assertIn(second_fix, variant_fixes)

    def test_repeating_same_variant_updates_existing_variant_instead_of_duplicating(self) -> None:
        first = self.app.issue_record_resolution(
            title="Relative sqlite path breaks outside repo root",
            raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
            canonical_fix="Resolve the SQLite path relative to __file__.",
            prevention_rule="No production DB path may depend on cwd.",
            project_scope="global",
            canonical_symptom="sqlite database path fails outside repo root",
            verification_steps="Run from repo root and external cwd.",
            tags="sqlite,path,cwd",
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            command="python -m app.main",
            file_path="services/db_loader.py",
            stack_excerpt='File "services/db_loader.py", line 12, in load_db',
            domain="python",
        )
        second = self.app.issue_record_resolution(
            title="Relative sqlite path breaks outside repo root",
            raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
            canonical_fix="Resolve the SQLite path relative to __file__ and normalize the final Path.",
            prevention_rule="No production DB path may depend on cwd.",
            project_scope="global",
            canonical_symptom="sqlite database path fails outside repo root",
            verification_steps="Run from repo root and external cwd.",
            tags="sqlite,path,cwd",
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            command="python -m app.main",
            file_path="services/db_loader.py",
            stack_excerpt='File "services/db_loader.py", line 12, in load_db',
            domain="python",
        )

        self.assertEqual(first["pattern_id"], second["pattern_id"])
        self.assertEqual(first["variant_id"], second["variant_id"])
        self.assertEqual(second["variant_action"], "updated")

        bundle = self.app.issue_get(pattern_id=first["pattern_id"], include_examples=True, examples_limit=10)
        self.assertEqual(len(bundle["variants"]), 1)
        self.assertEqual(bundle["variants"][0]["times_used"], 2)
        self.assertEqual(bundle["pattern"]["times_seen"], 2)

    def test_atomic_record_resolution_rolls_back_everything_on_episode_failure(self) -> None:
        with mock.patch.object(
            self.app.store,
            "_insert_episode_tx",
            side_effect=RuntimeError("synthetic episode failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.app.issue_record_resolution(
                    title="Relative sqlite path breaks outside repo root",
                    raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
                    canonical_fix="Resolve the SQLite path relative to __file__.",
                    prevention_rule="No production DB path may depend on cwd.",
                    project_scope="global",
                    canonical_symptom="sqlite database path fails outside repo root",
                    verification_steps="Run from repo root and external cwd.",
                    tags="sqlite,path,cwd",
                    error_family="sqlite_error",
                    root_cause_class="cwd_relative_path_bug",
                    command="python -m app.main",
                    file_path="services/db_loader.py",
                    domain="python",
                )

        with self.app.store.managed_connection() as conn:
            pattern_count = conn.execute("SELECT COUNT(*) AS count FROM issue_patterns").fetchone()["count"]
            variant_count = conn.execute("SELECT COUNT(*) AS count FROM issue_variants").fetchone()["count"]
            episode_count = conn.execute("SELECT COUNT(*) AS count FROM issue_episodes").fetchone()["count"]
            example_count = conn.execute("SELECT COUNT(*) AS count FROM issue_examples").fetchone()["count"]

        self.assertEqual(pattern_count, 0)
        self.assertEqual(variant_count, 0)
        self.assertEqual(episode_count, 0)
        self.assertEqual(example_count, 0)


if __name__ == "__main__":
    unittest.main()
