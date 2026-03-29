from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from codex_issue_memory.app import IssueMemoryApp


class SessionMemoryScopingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-session-scope-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_variant_specific_rejections_are_kept_independently(self) -> None:
        base = dict(
            title="Tensor cross-device mix in training pipeline",
            prevention_rule="Always move tensors to learner device at the module boundary.",
            project_scope="global",
            canonical_symptom="training update fails because some tensors remain on cpu while model is on cuda",
            verification_steps="Run one update and assert all tensors share the learner device.",
            tags="pytorch,tensor,device",
            error_family="tensor_device_error",
            root_cause_class="tensor_cross_device_mix",
            domain="ai",
            repo_name="repoA",
        )
        first = self.app.issue_record_resolution(
            raw_error="RuntimeError: Expected all tensors to be on the same device in PPO dataloader",
            canonical_fix="Move PPO batch to cuda in dataloader.",
            context="ppo dataloader variant",
            file_path="rl/ppo/buffer.py",
            command="python train_ppo.py --device cuda",
            git_commit="1",
            **base,
        )
        second = self.app.issue_record_resolution(
            raw_error="RuntimeError: Expected all tensors to be on the same device when restoring optimizer state",
            canonical_fix="Move optimizer state tensors to cuda after checkpoint load.",
            context="ppo checkpoint variant",
            file_path="rl/ppo/checkpoint.py",
            command="python train_ppo.py --resume latest --device cuda",
            git_commit="2",
            **base,
        )

        q1 = self.app.issue_match(
            error_text="RuntimeError: expected all tensors to be on same device during PPO dataloader batch prep",
            file_path="rl/ppo/buffer.py",
            command="python train_ppo.py --device cuda",
            session_id="variant-memory",
            repo_name="repoA",
        )
        self.app.issue_feedback(
            retrieval_event_id=q1["retrieval_event_id"],
            feedback_type="candidate_rejected",
            candidate_rank=1,
        )

        q2 = self.app.issue_match(
            error_text="RuntimeError: expected all tensors to be on same device after checkpoint resume",
            file_path="rl/ppo/checkpoint.py",
            command="python train_ppo.py --resume latest --device cuda",
            session_id="variant-memory",
            repo_name="repoA",
        )
        self.app.issue_feedback(
            retrieval_event_id=q2["retrieval_event_id"],
            feedback_type="candidate_rejected",
            candidate_rank=1,
        )

        snapshot = self.app.session_service.snapshot("variant-memory")
        keys = {row["memory_key"] for row in snapshot}
        self.assertIn(f"rejected_variant:{first['pattern_id']}:{first['variant_id']}", keys)
        self.assertIn(f"rejected_variant:{second['pattern_id']}:{second['variant_id']}", keys)
        self.assertEqual(len(snapshot), 2)

    def test_repo_specific_rejection_does_not_leak_across_repos(self) -> None:
        base = dict(
            title="Missing hub token for model bootstrap",
            prevention_rule="Validate model hub credentials before startup.",
            project_scope="global",
            canonical_symptom="bootstrap fails because required access token is invalid or absent",
            verification_steps="Call hub endpoint with configured token before startup.",
            tags="llm,auth,token",
            error_family="auth_error",
            root_cause_class="invalid_credentials",
            domain="llm",
        )
        self.app.issue_record_resolution(
            raw_error="403 Forbidden invalid token in repoA bootstrap",
            canonical_fix="Refresh token A",
            context="repo A",
            file_path="a/bootstrap.py",
            command="python repoA.py",
            repo_name="repoA",
            git_commit="1",
            **base,
        )
        self.app.issue_record_resolution(
            raw_error="403 Forbidden invalid token in repoB bootstrap",
            canonical_fix="Refresh token B",
            context="repo B",
            file_path="b/bootstrap.py",
            command="python repoB.py",
            repo_name="repoB",
            git_commit="1",
            **base,
        )

        match_a = self.app.issue_match(
            error_text="403 Forbidden invalid token during repoA bootstrap",
            file_path="a/bootstrap.py",
            command="python repoA.py",
            repo_name="repoA",
            session_id="shared-session",
        )
        self.app.issue_feedback(
            retrieval_event_id=match_a["retrieval_event_id"],
            feedback_type="candidate_rejected",
            candidate_rank=1,
        )

        match_b = self.app.issue_match(
            error_text="403 Forbidden invalid token during repoB bootstrap",
            file_path="b/bootstrap.py",
            command="python repoB.py",
            repo_name="repoB",
            session_id="shared-session",
        )
        self.assertEqual(match_b["matches"][0]["canonical_fix"], "Refresh token B")
        self.assertEqual(match_b["decision"]["status"], "match")
        self.assertNotIn("session-rejection-memory", match_b["matches"][0]["why"])


if __name__ == "__main__":
    unittest.main()
