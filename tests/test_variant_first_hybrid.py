from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp


class VariantFirstHybridTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="issue-memory-variant-hybrid-"
        )
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_two_tensor_variants(self) -> tuple[dict, dict]:
        first = self.app.issue_record_resolution(
            title="PyTorch tensors on mixed devices",
            raw_error="RuntimeError: Expected all tensors to be on the same device, but got cpu and cuda",
            canonical_fix="Move optimizer state tensors onto the active CUDA device after checkpoint resume.",
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
            canonical_fix="Move each dataloader batch to the model device before the forward pass.",
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
        return first, second

    def test_variant_first_retrieval_prefers_context_specific_variant(self) -> None:
        first, second = self._seed_two_tensor_variants()
        self.assertEqual(first["pattern_id"], second["pattern_id"])

        result = self.app.issue_match(
            error_text="RuntimeError: Expected all tensors to be on the same device, but got cpu and cuda",
            command="python train.py",
            file_path="data/dataloader.py",
            stack_excerpt='File "data/dataloader.py", line 18, in move_batch_to_device',
            env_json='{"python": "3.12", "torch": "2.3.1", "cuda": "12.1"}',
            repo_name="vision-trainer",
            git_commit="abc123def4567890",
            project_scope="global",
            limit=3,
        )

        self.assertEqual(result["decision"]["status"], "match")
        self.assertEqual(result["matches"][0]["pattern_id"], first["pattern_id"])
        self.assertEqual(result["matches"][0]["variant_id"], second["variant_id"])
        self.assertIn(
            "Move each dataloader batch", result["matches"][0]["canonical_fix"]
        )
        self.assertIn("variant-first-candidate", result["matches"][0]["why"])

        with self.app.store.managed_connection() as conn:
            retrieval_event = conn.execute(
                "SELECT * FROM retrieval_events WHERE id = ?",
                (result["retrieval_event_id"],),
            ).fetchone()
            candidate_rows = conn.execute(
                "SELECT * FROM retrieval_candidates WHERE retrieval_event_id = ? ORDER BY candidate_rank ASC",
                (result["retrieval_event_id"],),
            ).fetchall()

        self.assertIsNotNone(retrieval_event)
        assert retrieval_event is not None
        self.assertEqual(
            int(retrieval_event["selected_variant_id"]), second["variant_id"]
        )
        self.assertGreaterEqual(len(candidate_rows), 2)
        self.assertEqual(str(candidate_rows[0]["candidate_type"]), "variant")

    def test_feedback_weighted_consolidation_updates_high_quality_variant(self) -> None:
        title = "Relative sqlite path breaks outside repo root"
        first = self.app.issue_record_resolution(
            title=title,
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
            env_json='{"python": "3.12"}',
            repo_name="myrepo",
            domain="python",
        )
        second = self.app.issue_record_resolution(
            title=title,
            raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
            canonical_fix="Resolve the SQLite path relative to the report package root.",
            prevention_rule="No production DB path may depend on cwd.",
            project_scope="global",
            canonical_symptom="sqlite database path fails outside repo root",
            verification_steps="Run report entrypoint from repo root and external cwd.",
            tags="sqlite,path,cwd,report",
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            command="python report.py",
            file_path="reports/generate.py",
            stack_excerpt='File "reports/generate.py", line 7, in load_db',
            env_json='{"python": "3.12"}',
            repo_name="myrepo",
            domain="python",
        )

        for _ in range(3):
            match = self.app.issue_match(
                error_text="FileNotFoundError: references/contractsDatabase.sqlite3",
                command="python -m app.main",
                file_path="services/db_loader.py",
                env_json='{"python": "3.12"}',
                repo_name="myrepo",
                project_scope="global",
                limit=3,
            )
            self.app.issue_feedback(
                retrieval_event_id=match["retrieval_event_id"],
                feedback_type="candidate_rejected",
                candidate_rank=1,
                notes="Synthetic negative feedback for the loader-specific variant.",
            )

        for _ in range(4):
            match = self.app.issue_match(
                error_text="FileNotFoundError: references/contractsDatabase.sqlite3",
                command="python report.py",
                file_path="reports/generate.py",
                env_json='{"python": "3.12"}',
                repo_name="myrepo",
                project_scope="global",
                limit=3,
            )
            self.app.issue_feedback(
                retrieval_event_id=match["retrieval_event_id"],
                feedback_type="fix_verified",
                candidate_rank=1,
                notes="Synthetic positive feedback for the report-specific variant.",
            )

        consolidated = self.app.issue_record_resolution(
            title=title,
            raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
            canonical_fix="Use the report package root when resolving the sqlite path.",
            prevention_rule="No production DB path may depend on cwd.",
            project_scope="global",
            canonical_symptom="sqlite database path fails outside repo root",
            verification_steps="Run from multiple cwd values.",
            tags="sqlite,path,cwd",
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            env_json='{"python": "3.12"}',
            repo_name="myrepo",
            domain="python",
        )

        self.assertEqual(consolidated["pattern_id"], first["pattern_id"])
        self.assertEqual(consolidated["variant_id"], second["variant_id"])
        self.assertEqual(consolidated["variant_action"], "updated")
        self.assertEqual(
            consolidated["consolidation"]["matched_variant_id"], second["variant_id"]
        )
        self.assertIn("feedback-weight", consolidated["consolidation"]["reasons"])
        self.assertLess(consolidated["consolidation"]["score"], 0.5)

        bundle = self.app.issue_get(
            pattern_id=first["pattern_id"], include_examples=True, examples_limit=10
        )
        variants_by_id = {row["id"]: row for row in bundle["variants"]}
        self.assertGreater(
            variants_by_id[second["variant_id"]]["success_count"],
            variants_by_id[first["variant_id"]]["success_count"],
        )
        self.assertEqual(len(bundle["variants"]), 2)


if __name__ == "__main__":
    unittest.main()
