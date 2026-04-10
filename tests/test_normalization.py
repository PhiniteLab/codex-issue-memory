from __future__ import annotations

import unittest

from codex_issue_memory.normalization import (
    build_query_profile,
    extract_exception_types,
    make_env_fingerprint,
    make_stack_signature,
)


class NormalizationTests(unittest.TestCase):
    def test_exception_types_are_canonicalized(self) -> None:
        text = (
            "Traceback (most recent call last):\n"
            "FileNotFoundError: missing config.yml\n"
            "filenotfounderror raised again in retry path\n"
            "RuntimeWarning: fallback mode enabled"
        )
        self.assertEqual(
            extract_exception_types(text),
            ["filenotfounderror", "runtimewarning"],
        )

    def test_build_query_profile_adds_split_tokens_and_fingerprints(self) -> None:
        profile = build_query_profile(
            error_text="RuntimeError: Expected all tensors to be on the same device, got cpu and cuda",
            context="Happens after optimizer resume from checkpoint",
            command="python train.py --resume outputs/ckpt.pt",
            file_path="trainer/checkpoint_loader.py",
            stack_excerpt='File "trainer/checkpoint_loader.py", line 42, in restore_optimizer_state',
            env_json='{"python": "3.12", "torch": "2.3.1", "cuda": "12.1"}',
            repo_name="vision-trainer",
            git_commit="abc123def4567890",
        )
        self.assertIn("runtimeerror", profile.exception_types)
        self.assertTrue(profile.symptom_tokens)
        self.assertIn("resume", profile.command_tokens)
        self.assertIn("checkpoint_loader.py", profile.path_tokens)
        self.assertTrue(profile.stack_signature.startswith("stk:"))
        self.assertTrue(profile.env_fingerprint.startswith("env:"))
        self.assertTrue(profile.repo_fingerprint.startswith("repo:"))
        self.assertTrue(profile.command_signature.startswith("cmd:"))
        self.assertTrue(profile.path_signature.startswith("path:"))
        self.assertEqual(profile.error_family, "tensor_device_error")
        self.assertEqual(profile.root_cause_class, "tensor_cross_device_mix")
        self.assertEqual(profile.project_scope, "global")
        self.assertEqual(profile.entity_slots.get("device_from"), "cpu")
        self.assertEqual(profile.entity_slots.get("device_to"), "cuda")
        self.assertIn("move_batch_to_device_boundary", profile.strategy_hints)

    def test_stack_and_env_fingerprints_are_stable(self) -> None:
        stack_a = 'File "a/b/train.py", line 10, in step\nRuntimeError: boom'
        stack_b = 'File "a\\b\\train.py", line 10, in step\nRuntimeError: boom'
        self.assertEqual(make_stack_signature(stack_a), make_stack_signature(stack_b))

        env_a = make_env_fingerprint(
            '{"torch": "2.3.1", "python": "3.12"}', command="python train.py"
        )
        env_b = make_env_fingerprint(
            '{"python": "3.12", "torch": "2.3.1"}', command="python train.py"
        )
        self.assertEqual(env_a, env_b)

    def test_profile_carries_user_scope_and_module_entity(self) -> None:
        profile = build_query_profile(
            error_text="ModuleNotFoundError: No module named requests",
            context="Fails inside the research tooling virtualenv",
            command="python tools/check_env.py",
            project_scope="research",
            user_scope="mehmet",
        )
        self.assertEqual(profile.project_scope, "research")
        self.assertEqual(profile.user_scope, "mehmet")
        self.assertEqual(profile.entity_slots.get("module_name"), "requests")
        self.assertIn("install_missing_dependency", profile.strategy_hints)


if __name__ == "__main__":
    unittest.main()
