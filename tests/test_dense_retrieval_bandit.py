from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.benchmarks import run_dense_bandit_benchmark, seed_dense_bandit_memory


class DenseRetrievalBanditTests(unittest.TestCase):
    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL",
        "ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-dense-bandit-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_ENABLE_DENSE_RETRIEVAL"] = "1"
        os.environ["ISSUE_MEMORY_ENABLE_STRATEGY_BANDIT"] = "1"
        self.app = IssueMemoryApp()
        seed_dense_bandit_memory(self.app)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_dense_retrieval_surfaces_dense_reason_for_paraphrase_queries(self) -> None:
        result = self.app.issue_match(
            error_text=(
                "active runtime uses an incompatible base checkpoint for the low-rank "
                "adapter during local llm warm start"
            ),
            file_path="llm/lora/load_adapter.py",
            command="python train_lora.py --resume adapter.ckpt",
            repo_name="llm-lab",
            project_scope="global",
            limit=3,
        )
        self.assertTrue(result["matches"])
        self.assertEqual(result["matches"][0]["title"], "LoRA adapter checkpoint rank mismatch")
        self.assertIn("dense-retrieval", result["matches"][0]["why"])

        with self.app.store.managed_connection() as conn:
            embedding_count = conn.execute(
                "SELECT COUNT(*) AS count FROM embeddings WHERE object_type = 'variant'"
            ).fetchone()["count"]
        self.assertGreater(int(embedding_count), 0)

    def test_strategy_bandit_promotes_verified_second_candidate(self) -> None:
        first = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests",
            project_scope="global",
            session_id="bandit-before",
            repo_name="tooling-lab",
            limit=3,
        )
        self.assertEqual(first["decision"]["status"], "ambiguous")
        second_candidate_pattern = int(first["matches"][1]["pattern_id"])

        feedback = self.app.issue_feedback(
            retrieval_event_id=int(first["retrieval_event_id"]),
            feedback_type="fix_verified",
            candidate_rank=2,
            notes="Positive strategy-bandit training for second candidate.",
        )
        self.assertIsNotNone(feedback["bandit"])
        assert feedback["bandit"] is not None
        self.assertEqual(feedback["bandit"]["policy"], "conservative_hierarchical_thompson")
        self.assertTrue(feedback["bandit"]["global_update_applied"])

        second = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests",
            project_scope="global",
            session_id="bandit-after",
            repo_name="tooling-lab",
            limit=3,
        )
        self.assertTrue(second["matches"])
        self.assertEqual(int(second["matches"][0]["pattern_id"]), second_candidate_pattern)
        self.assertIn("strategy-bandit-safe-override", second["matches"][0]["why"])

    def test_dense_bandit_benchmark_summary(self) -> None:
        report = run_dense_bandit_benchmark(self.app, repeats=2)
        self.assertGreaterEqual(float(report["dense_top1_accuracy"]), 1.0)
        self.assertGreater(float(report["dense_reason_rate"]), 0.0)
        self.assertTrue(report["bandit"]["promoted_second_candidate"])


if __name__ == "__main__":
    unittest.main()
