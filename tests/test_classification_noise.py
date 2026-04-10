from __future__ import annotations

import unittest

from codex_issue_memory.normalization import build_query_profile


class ClassificationNoiseTests(unittest.TestCase):
    def test_auth_query_with_file_path_is_not_misclassified_as_path_error(self) -> None:
        profile = build_query_profile(
            error_text="403 Forbidden invalid token while downloading tokenizer artifacts",
            file_path="llm/tokenizer/bootstrap.py",
            command="python download_tokenizer.py --model llama3",
            repo_name="llm-finetune-lab",
        )
        self.assertEqual(profile.error_family, "auth_error")
        self.assertEqual(profile.root_cause_class, "invalid_credentials")
        self.assertNotIn("missing-file-signals", profile.evidence)
        self.assertNotIn("path", profile.tags)

    def test_tensor_device_query_with_file_path_is_not_misclassified_as_path_error(
        self,
    ) -> None:
        profile = build_query_profile(
            error_text="RuntimeError: expected all tensors to be on the same device during SAC replay update",
            file_path="rl/sac/replay_buffer.py",
            command="python train_sac.py --device cuda",
            repo_name="rl-control-lab",
        )
        self.assertEqual(profile.error_family, "tensor_device_error")
        self.assertEqual(profile.root_cause_class, "tensor_cross_device_mix")
        self.assertNotIn("missing-file-signals", profile.evidence)
        self.assertNotIn("path", profile.tags)


if __name__ == "__main__":
    unittest.main()
