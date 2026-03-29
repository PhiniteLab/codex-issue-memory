from __future__ import annotations

import unittest

from codex_issue_memory.normalization import make_pattern_key, make_variant_key


class SignatureSplitTests(unittest.TestCase):
    def test_variant_key_splits_same_pattern_into_distinct_contexts(self) -> None:
        pattern_key = make_pattern_key(
            project_scope="global",
            error_family="tensor_device_error",
            root_cause_class="tensor_cross_device_mix",
            canonical_symptom="mixed cuda and cpu tensors during training",
            title="PyTorch tensors on mixed devices",
            tags=["pytorch", "cuda", "cpu"],
        )
        variant_a = make_variant_key(
            pattern_key=pattern_key,
            command="python train.py --resume outputs/ckpt.pt",
            file_path="trainer/checkpoint_loader.py",
            stack_excerpt='File "trainer/checkpoint_loader.py", line 42, in restore_optimizer_state',
            env_json='{"python": "3.12", "torch": "2.3.1", "cuda": "12.1"}',
            repo_name="vision-trainer",
            git_commit="abc123def4567890",
        )
        variant_b = make_variant_key(
            pattern_key=pattern_key,
            command="python train.py",
            file_path="data/dataloader.py",
            stack_excerpt='File "data/dataloader.py", line 18, in move_batch_to_device',
            env_json='{"python": "3.12", "torch": "2.3.1", "cuda": "12.1"}',
            repo_name="vision-trainer",
            git_commit="abc123def4567890",
        )
        self.assertNotEqual(variant_a, variant_b)
        self.assertTrue(variant_a.startswith(pattern_key + "|variant:"))
        self.assertTrue(variant_b.startswith(pattern_key + "|variant:"))


if __name__ == "__main__":
    unittest.main()
