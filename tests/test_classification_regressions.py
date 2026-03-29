from __future__ import annotations

import unittest

from codex_issue_memory.normalization.classify import classify_from_text


class ClassificationRegressionTests(unittest.TestCase):
    def test_tensor_shape_symptom_is_detected(self) -> None:
        family, root, _tags, _evidence = classify_from_text(
            "ppo minibatch update fails because value head receives rank mismatched advantage tensor after refactor"
        )
        self.assertEqual(family, "tensor_shape_error")
        self.assertEqual(root, "tensor_rank_or_dim_mismatch")

    def test_path_resolution_symptom_is_detected(self) -> None:
        family, root, _tags, _evidence = classify_from_text(
            "hjb solver cannot load precomputed value grid when started outside the repository root"
        )
        self.assertEqual(family, "path_resolution_error")
        self.assertEqual(root, "cwd_relative_path_bug")

    def test_json_symptom_is_detected(self) -> None:
        family, root, _tags, _evidence = classify_from_text(
            "rag indexing fails because serialized chunk metadata is malformed json"
        )
        self.assertEqual(family, "json_error")
        self.assertEqual(root, "serialization_contract_mismatch")

    def test_environment_symptom_is_detected(self) -> None:
        family, root, _tags, _evidence = classify_from_text(
            "prompt evaluation pipeline fails because required api key env var is absent"
        )
        self.assertEqual(family, "environment_error")
        self.assertEqual(root, "missing_env_var")

    def test_lora_shape_symptom_is_detected(self) -> None:
        family, root, _tags, _evidence = classify_from_text(
            "lora adapter load fails because checkpoint rank or base model differs from the active llm"
        )
        self.assertEqual(family, "tensor_shape_error")
        self.assertEqual(root, "tensor_rank_or_dim_mismatch")

    def test_plain_language_style_feedback_is_not_dtype_error(self) -> None:
        family, root, _tags, _evidence = classify_from_text(
            "the introduction paragraph feels too long and needs better academic tone"
        )
        self.assertEqual(family, "generic_runtime_error")
        self.assertEqual(root, "unknown")

    def test_ssh_publickey_maps_to_auth_not_docx_permission(self) -> None:
        family, root, _tags, _evidence = classify_from_text(
            "permission denied (publickey) while ssh into git remote"
        )
        self.assertEqual(family, "auth_error")
        self.assertEqual(root, "ssh_publickey_auth_failure")

    def test_cuda_driver_compatibility_is_not_tensor_cross_device(self) -> None:
        family, root, _tags, _evidence = classify_from_text(
            "cuda driver version is insufficient for cuda runtime version"
        )
        self.assertEqual(family, "environment_error")
        self.assertEqual(root, "cuda_runtime_compatibility")


if __name__ == "__main__":
    unittest.main()
